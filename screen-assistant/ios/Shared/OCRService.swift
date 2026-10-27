import Vision
import CoreGraphics
import Foundation

/// Vision 中文 OCR 服务（架构无关组件）。
enum OCRService {
    /// 广播端只允许一个 OCR 作业。工作队列串行，忙时拒绝新图像，不让图像排队滞留。
    private static let queue = DispatchQueue(label: "com.screenassistant.ocr", qos: .userInitiated)
    private static let stateLock = NSLock()
    private static var isRunning = false

    struct TextLine {
        let text: String
        /// Vision 坐标，原点在左下角，范围 0...1，相对当前裁剪图像。
        let boundingBox: CGRect
        let confidence: Float
    }

    struct RegionInput {
        let identifier: String
        let image: CGImage?
    }

    struct RegionResult {
        let identifier: String
        let lines: [TextLine]
    }

    /// 单次识别的取消句柄。Vision 执行期间取消请求，并确保迟到结果不再调用完成闭包。
    final class CancellationToken {
        private let lock = NSLock()
        private var cancelled = false
        private var request: VNRecognizeTextRequest?
        private var completion: (([RegionResult]) -> Void)?

        fileprivate init(completion: @escaping ([RegionResult]) -> Void) {
            self.completion = completion
        }

        fileprivate var isCancelled: Bool {
            lock.lock()
            defer { lock.unlock() }
            return cancelled
        }

        fileprivate func attach(_ request: VNRecognizeTextRequest) {
            lock.lock()
            let shouldCancel = cancelled
            if !shouldCancel { self.request = request }
            lock.unlock()
            if shouldCancel { request.cancel() }
        }

        func cancel() {
            lock.lock()
            cancelled = true
            completion = nil
            let request = self.request
            self.request = nil
            lock.unlock()
            request?.cancel()
        }

        fileprivate func finish(_ results: [RegionResult]) {
            lock.lock()
            guard !cancelled, let completion else {
                lock.unlock()
                return
            }
            self.completion = nil
            request = nil
            lock.unlock()
            completion(results)
        }
    }

    /// 在同一串行OCR作业中逐块处理内存裁剪图。全链路仍单在途，忙时丢帧不排队。
    @discardableResult
    static func recognize(regions: [RegionInput],
                          completion: @escaping ([RegionResult]) -> Void) -> CancellationToken? {
        stateLock.lock()
        guard !isRunning else {
            stateLock.unlock()
            return nil
        }
        isRunning = true
        stateLock.unlock()

        let token = CancellationToken(completion: completion)
        queue.async {
            defer {
                stateLock.lock()
                isRunning = false
                stateLock.unlock()
            }
            autoreleasepool {
                var results: [RegionResult] = []
                for region in regions {
                    guard !token.isCancelled else { return }
                    guard let cgImage = region.image else {
                        results.append(RegionResult(identifier: region.identifier, lines: []))
                        continue
                    }
                    let request = VNRecognizeTextRequest()
                    request.recognitionLevel = .accurate
                    request.usesLanguageCorrection = false
                    request.preferBackgroundProcessing = true
                    // iOS 15 中文保持 accurate；语言配置沿用现有实测结构。
                    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
                    token.attach(request)
                    guard !token.isCancelled else { return }
                    do {
                        try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
                    } catch {
                        results.append(RegionResult(identifier: region.identifier, lines: []))
                        continue
                    }
                    let observations = request.results as? [VNRecognizedTextObservation] ?? []
                    let sorted = observations.sorted { a, b in
                        let dy = abs(a.boundingBox.minY - b.boundingBox.minY)
                        if dy > 0.01 { return a.boundingBox.minY > b.boundingBox.minY }
                        return a.boundingBox.minX < b.boundingBox.minX
                    }
                    let lines = sorted.compactMap { observation -> TextLine? in
                        guard let candidate = observation.topCandidates(1).first else { return nil }
                        return TextLine(text: candidate.string, boundingBox: observation.boundingBox,
                                        confidence: candidate.confidence)
                    }
                    results.append(RegionResult(identifier: region.identifier, lines: lines))
                }
                token.finish(results)
            }
        }
        return token
    }
}
