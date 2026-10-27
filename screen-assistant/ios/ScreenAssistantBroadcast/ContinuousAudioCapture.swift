import Foundation
import ReplayKit
import CoreMedia
import AVFAudio

/// ReplayKit薄采集：回调最多4份自有复制，转换/打包/网络都在worker。
/// 每源最多一个600ms聚合块、两个待发包、一个HTTP在途；从不录文件。
final class ContinuousAudioCapture {
    private struct Context {
        let generation: String
        let settings: AudioCaptureSettings
        let credentials: LiveSessionCredentials
        let baseURL: URL
    }
    private struct Owned {
        let context: Context
        let source: String
        let sequence: Int64
        let pts: CMTime
        let observedAtMS: Int64
        let frames: AVAudioFrameCount
        let format: AVAudioFormat
        let originalFormat: [String: Any]
        let buffers: [Data]
        let gaps: [String]
    }
    private final class Track {
        let id = UUID().uuidString.lowercased()
        let context: Context
        let source: String
        let format: AVAudioFormat
        let originalFormat: [String: Any]
        let converter: AVAudioConverter
        var expectedPTS: CMTime?
        var expectedSequence: Int64?
        var anchor: [String: Any]?
        var pcm = Data()
        var spans: [[String: Any]] = []
        var sequence = 0
        var gaps: Set<String> = []
        var pending: [[String: Any]] = []
        var request: URLSessionDataTask?
        var retryAfter: TimeInterval = 0

        init?(_ owned: Owned) {
            guard let output = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000,
                                              channels: 1, interleaved: true),
                  let converter = AVAudioConverter(from: owned.format, to: output) else { return nil }
            context = owned.context
            source = owned.source
            format = owned.format
            originalFormat = owned.originalFormat
            self.converter = converter
            converter.primeMethod = .none
        }
    }

    private let lock = NSLock()
    private let copies = DispatchSemaphore(value: 4)
    private let worker = DispatchQueue(label: "com.screenassistant.audio", qos: .utility)
    private var running = false
    private var generation = UUID().uuidString.lowercased()
    private var context: Context?
    private var sourceSequences: [String: Int64] = [:]
    private var callbackGaps: [String: Set<String>] = [:]
    private var callbackDrops = 0
    private var tracks: [String: Track] = [:] // worker only
    private var status = AudioCaptureStatus() // worker only
    private var timer: DispatchSourceTimer?
    private let session: URLSession

    init() {
        let config = URLSessionConfiguration.ephemeral
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 5
        config.timeoutIntervalForResource = 10
        config.urlCache = nil
        session = URLSession(configuration: config)
        let timer = DispatchSource.makeTimerSource(queue: worker)
        timer.schedule(deadline: .now(), repeating: .milliseconds(500))
        timer.setEventHandler { [weak self] in self?.refresh() }
        timer.resume()
        self.timer = timer
    }

    deinit {
        timer?.cancel()
        session.invalidateAndCancel()
    }

    /// 生命周期立即阻断回调，worker取消旧传输；暂停/结束时仅尽力送有界尾包。
    func transition(running: Bool) {
        lock.lock()
        self.running = running
        generation = UUID().uuidString.lowercased()
        context = nil
        sourceSequences.removeAll()
        callbackGaps.removeAll()
        lock.unlock()
        worker.async { [weak self] in
            guard let self else { return }
            for track in self.tracks.values {
                let hadUnsent = track.request != nil || !track.pending.isEmpty
                track.request?.cancel()
                track.request = nil
                track.pending.removeAll()
                if !running {
                    if hadUnsent { track.gaps.insert("broadcast_boundary_with_unsent_audio") }
                    self.flush(track, final: true)
                }
            }
            // 旧包绑定旧credentials；完成闭包不得重新读取/套用新场次令牌。
            self.tracks.removeAll()
            if running { self.status = AudioCaptureStatus() }
            self.refresh()
        }
    }

    /// 不读文件、不转换、不联网、不保留CMSampleBuffer/CMBlockBuffer。
    func observe(_ sample: CMSampleBuffer, type: RPSampleBufferType) {
        let source: String
        switch type {
        case .audioApp: source = "audioApp"
        case .audioMic: source = "audioMic"
        default: return
        }
        lock.lock()
        guard let current = context, running, current.settings.includes(source) else {
            lock.unlock(); return
        }
        let sequence = sourceSequences[source, default: 0]
        sourceSequences[source] = sequence + 1
        lock.unlock()
        guard copies.wait(timeout: .now()) == .success else {
            markGap(source, "copy_queue_full"); return
        }
        guard let owned = copy(sample, context: current, source: source, sequence: sequence) else {
            copies.signal()
            markGap(source, "unsupported_or_oversized_pcm"); return
        }
        worker.async { [weak self] in
            guard let self else { return }
            defer { self.copies.signal() }
            autoreleasepool { self.consume(owned) }
        }
    }

    private func markGap(_ source: String, _ reason: String) {
        lock.lock()
        callbackGaps[source, default: []].insert(reason)
        callbackDrops += 1
        lock.unlock()
    }

    private func refresh() {
        let settings = AudioCaptureSettings.load()
        let credentials = LiveSessionState.isEnabled() ? LiveSessionState.credentials() : nil
        let baseURL = SharedConfig.serverBaseURL()
        lock.lock()
        let active = running
        let generation = self.generation
        let old = context
        if active, settings.enabled, let credentials, let baseURL {
            context = Context(generation: generation, settings: settings,
                              credentials: credentials, baseURL: baseURL)
        } else { context = nil }
        let current = context
        let dropped = callbackDrops
        callbackDrops = 0
        lock.unlock()
        if old?.settings.id != current?.settings.id ||
            old?.credentials.sessionID != current?.credentials.sessionID ||
            old?.credentials.token != current?.credentials.token || old?.baseURL != current?.baseURL {
            for track in tracks.values { track.request?.cancel() }
            tracks.removeAll()
            if old?.credentials.sessionID != current?.credentials.sessionID { status = AudioCaptureStatus() }
        }
        status.dropped += dropped
        status.phase = current == nil ? (settings.enabled ? "等待广播和本场辅助" : "未启用") : "接收系统PCM；来源需核对"
        status.save()
    }

    private func copy(_ sample: CMSampleBuffer, context: Context,
                      source: String, sequence: Int64) -> Owned? {
        guard CMSampleBufferIsValid(sample), CMSampleBufferDataIsReady(sample),
              let description = CMSampleBufferGetFormatDescription(sample),
              var asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee,
              asbd.mFormatID == kAudioFormatLinearPCM,
              asbd.mSampleRate.isFinite, (8000...96000).contains(asbd.mSampleRate),
              asbd.mSampleRate.rounded() == asbd.mSampleRate,
              (1...2).contains(asbd.mChannelsPerFrame),
              asbd.mFormatFlags & kAudioFormatFlagIsBigEndian == 0,
              asbd.mFormatFlags & kAudioFormatFlagIsPacked != 0 else { return nil }
        let floating = asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0
        let signed = asbd.mFormatFlags & kAudioFormatFlagIsSignedInteger != 0
        guard (floating && asbd.mBitsPerChannel == 32) ||
                (!floating && signed && asbd.mBitsPerChannel == 16) else { return nil }
        let frames = CMSampleBufferGetNumSamples(sample)
        // 100ms/至多76,800 bytes；异常大回调丢弃并标gap，不在回调做切片。
        guard frames > 0, frames <= Int(asbd.mSampleRate / 10),
              let format = AVAudioFormat(streamDescription: &asbd) else { return nil }
        let pts = CMSampleBufferGetPresentationTimeStamp(sample)
        guard pts.isValid, !pts.isIndefinite, pts.timescale > 0, pts.value >= 0, pts.epoch >= 0 else { return nil }
        let capacity = Int(asbd.mChannelsPerFrame)
        let bytes = MemoryLayout<AudioBufferList>.size + (capacity-1)*MemoryLayout<AudioBuffer>.stride
        let memory = UnsafeMutableRawPointer.allocate(byteCount: bytes,
                                      alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { memory.deallocate() }
        let list = memory.bindMemory(to: AudioBufferList.self, capacity: 1)
        list.initialize(to: AudioBufferList(mNumberBuffers: 0,
            mBuffers: AudioBuffer(mNumberChannels: 0, mDataByteSize: 0, mData: nil)))
        var block: CMBlockBuffer?
        defer { withExtendedLifetime(block) {} }
        guard CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sample, bufferListSizeNeededOut: nil, bufferListOut: list, bufferListSize: bytes,
            blockBufferAllocator: kCFAllocatorDefault, blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment),
            blockBufferOut: &block) == noErr,
            list.pointee.mNumberBuffers > 0, list.pointee.mNumberBuffers <= asbd.mChannelsPerFrame else { return nil }
        let interleaved = asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved == 0
        let buffers = UnsafeMutableAudioBufferListPointer(list)
        guard buffers.count == (interleaved ? 1 : capacity) else { return nil }
        var copied: [Data] = []
        for buffer in buffers {
            let count = frames * Int(asbd.mBitsPerChannel / 8) * (interleaved ? capacity : 1)
            guard let data = buffer.mData, Int(buffer.mDataByteSize) == count else { return nil }
            copied.append(Data(bytes: data, count: count))
        }
        lock.lock()
        let gaps = Array(callbackGaps.removeValue(forKey: source) ?? [])
        lock.unlock()
        return Owned(context: context, source: source, sequence: sequence, pts: pts,
                     observedAtMS: Int64(Date().timeIntervalSince1970*1000),
                     frames: AVAudioFrameCount(frames), format: format,
                     originalFormat: ["sample_rate": Int(asbd.mSampleRate), "channels": capacity,
                                      "sample_format": floating ? "f32le" : "s16le", "interleaved": interleaved],
                     buffers: copied, gaps: gaps)
    }

    private func consume(_ owned: Owned) {
        lock.lock()
        let valid = context?.generation == owned.context.generation &&
            context?.settings.id == owned.context.settings.id &&
            context?.credentials.sessionID == owned.context.credentials.sessionID &&
            context?.credentials.token == owned.context.credentials.token &&
            context?.baseURL == owned.context.baseURL
        lock.unlock()
        guard valid else { return }
        var track = tracks[owned.source]
        var discontinuous = !owned.gaps.isEmpty
        if let existing = track {
            if !existing.format.isEqual(owned.format) { discontinuous = true }
            if let expected = existing.expectedPTS {
                let delta = CMTimeSubtract(owned.pts, expected)
                if owned.pts.epoch != expected.epoch || !delta.seconds.isFinite ||
                    abs(delta.seconds) > 2/owned.format.sampleRate { discontinuous = true }
            }
            if let expected = existing.expectedSequence, expected != owned.sequence { discontinuous = true }
        }
        if discontinuous {
            track?.request?.cancel()
            tracks.removeValue(forKey: owned.source)
            track = nil
            status.dropped += 1
        }
        if track == nil {
            track = Track(owned)
            guard let track else { status.issue = "系统PCM转换器不可用"; return }
            track.gaps.formUnion(owned.gaps)
            if discontinuous { track.gaps.insert("capture_discontinuity") }
            track.anchor = ["pts": mediaTime(owned.pts), "observed_at_ms": owned.observedAtMS -
                Int64((Double(owned.frames)/owned.format.sampleRate*1000).rounded())]
            tracks[owned.source] = track
        }
        guard let track,
              let input = AVAudioPCMBuffer(pcmFormat: owned.format, frameCapacity: owned.frames) else { return }
        input.frameLength = owned.frames
        let targetBuffers = UnsafeMutableAudioBufferListPointer(input.mutableAudioBufferList)
        guard targetBuffers.count == owned.buffers.count else { markGap(owned.source, "buffer_layout"); return }
        for (index, bytes) in owned.buffers.enumerated() {
            guard let target = targetBuffers[index].mData,
                  Int(targetBuffers[index].mDataByteSize) >= bytes.count else { markGap(owned.source, "buffer_size"); return }
            bytes.withUnsafeBytes { data in target.copyMemory(from: data.baseAddress!, byteCount: bytes.count) }
        }
        let capacity = AVAudioFrameCount(ceil(Double(owned.frames)*16000/owned.format.sampleRate) + 64)
        guard let output = AVAudioPCMBuffer(pcmFormat: track.converter.outputFormat, frameCapacity: capacity) else { return }
        var supplied = false
        var error: NSError?
        let result = track.converter.convert(to: output, error: &error) { _, state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true
            state.pointee = .haveData
            return input
        }
        guard result != .error, error == nil, output.frameLength > 0,
              let samples = output.int16ChannelData?[0] else {
            markGap(owned.source, "conversion_failed"); status.issue = "系统PCM转换失败"; return
        }
        let converted = Data(bytes: samples, count: Int(output.frameLength)*2)
        if track.pcm.count + converted.count > 19200 || track.spans.count >= 128 { flush(track, final: false) }
        track.pcm.append(converted)
        track.spans.append(["sequence": owned.sequence, "pts": mediaTime(owned.pts), "frames": Int(owned.frames)])
        track.expectedSequence = owned.sequence+1
        track.expectedPTS = CMTimeAdd(owned.pts, CMTime(value: Int64(owned.frames),
                                               timescale: CMTimeScale(owned.format.sampleRate)))
        if owned.source == "audioMic" { status.copiedMic += 1 } else { status.copiedApp += 1 }
        if track.pcm.count >= 15360 { flush(track, final: false) } // 480ms，给大回调留余量
    }

    private func mediaTime(_ pts: CMTime) -> [String: Any] {
        ["value": pts.value, "timescale": Int(pts.timescale), "epoch": pts.epoch]
    }

    private func flush(_ track: Track, final: Bool) {
        guard final || (!track.pcm.isEmpty && !track.spans.isEmpty) else { return }
        var packet: [String: Any] = [
            "stream_id": track.id, "source": track.source, "sequence": track.sequence,
            "sample_rate": 16000, "channels": 1, "sample_format": "s16le",
            "pcm_base64": track.pcm.base64EncodedString(), "original_format": track.originalFormat,
            "spans": track.spans, "gap_before": Array(track.gaps), "final": final,
            "host_confirmed": track.context.settings.hostConfirmed(track.source)
        ]
        if let anchor = track.anchor { packet["clock_anchor"] = anchor }
        track.sequence += 1
        track.pcm.removeAll(keepingCapacity: true)
        track.spans.removeAll(keepingCapacity: true)
        track.gaps.removeAll()
        guard track.pending.count < 2, Date().timeIntervalSince1970 >= track.retryAfter else {
            status.dropped += 1
            track.gaps.insert("upload_queue_full_or_backoff")
            return
        }
        track.pending.append(packet)
        sendNext(track)
    }

    private func sendNext(_ track: Track) {
        guard track.request == nil, !track.pending.isEmpty else { return }
        // 发送时再核对令牌文件；停止或换场不会将旧PCM换上新令牌。
        guard let current = LiveSessionState.credentials(), LiveSessionState.isEnabled(),
              current.sessionID == track.context.credentials.sessionID,
              current.token == track.context.credentials.token,
              AudioCaptureSettings.load().id == track.context.settings.id else {
            track.pending.removeAll(); return
        }
        let payload = track.pending.removeFirst()
        var url = track.context.baseURL
        for component in ["v1", "live", "sessions", current.sessionID, "audio"] {
            url.appendPathComponent(component)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(track.context.credentials.token, forHTTPHeaderField: "X-Live-Session-Token")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        guard request.httpBody != nil else { track.gaps.insert("serialization_failed"); return }
        let task = session.dataTask(with: request) { [weak self, weak track] data, response, error in
            guard let self, let track else { return }
            self.worker.async {
                guard self.tracks[track.source] === track else { return }
                track.request = nil
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                if error == nil && code == 200 {
                    self.status.uploaded += 1
                    let body = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
                    let gaps = body?["gaps"] as? [String] ?? []
                    self.status.issue = gaps.contains("analysis_failed") ? "ASR分析未完成；仅PCM收件成功" : nil
                } else {
                    self.status.issue = "PCM收件失败 HTTP \(code)；语音未验收"
                    self.status.dropped += 1 + track.pending.count
                    track.pending.removeAll()
                    track.gaps.insert("upload_failed")
                    track.retryAfter = Date().timeIntervalSince1970 + 2
                }
                self.status.save()
                // transition后track不再属于当前代次，不延续发送旧排队包。
                if self.tracks[track.source] === track { self.sendNext(track) }
            }
        }
        track.request = task
        task.resume()
    }
}
