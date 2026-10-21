import Vision
import CoreGraphics
import Foundation

/// Vision 中文 OCR 服务（架构无关组件）。
enum OCRService {
    /// 广播端只允许一个 OCR 作业。工作队列串行，忙时拒绝新图像，不让图像排队滞留。
    private static let queue = DispatchQueue(label: "com.screenassistant.ocr", qos: .userInitiated)
    private static let stateLock = NSLock()
    private static var isRunning = false

    /// 单次识别的取消句柄。Vision 执行期间取消请求，并确保迟到结果不再调用完成闭包。
    final class CancellationToken {
        private let lock = NSLock()
        private var cancelled = false
        private var request: VNRecognizeTextRequest?
        private var completion: ((String) -> Void)?

        fileprivate init(completion: @escaping (String) -> Void) {
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

        fileprivate func finish(_ text: String) {
            lock.lock()
            guard !cancelled, let completion else {
                lock.unlock()
                return
            }
            self.completion = nil
            request = nil
            lock.unlock()
            completion(text)
        }
    }

    /// 对裁剪后的 CGImage 执行中文 OCR，按近似阅读顺序返回行拼接文本。
    /// 识别文本仅存在于闭包链内存，不保存。返回 nil 表示已有作业在途，调用方应丢弃本帧。
    @discardableResult
    static func recognizeText(in cgImage: CGImage,
                              completion: @escaping (String) -> Void) -> CancellationToken? {
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
                guard !token.isCancelled else { return }

                let request = VNRecognizeTextRequest { request, error in
                    guard error == nil,
                          let observations = request.results as? [VNRecognizedTextObservation] else {
                        token.finish("")
                        return
                    }
                    let sorted = observations.sorted { a, b in
                        let dy = abs(a.boundingBox.minY - b.boundingBox.minY)
                        if dy > 0.01 { return a.boundingBox.minY > b.boundingBox.minY }
                        return a.boundingBox.minX < b.boundingBox.minX
                    }
                    let lines = sorted.compactMap { $0.topCandidates(1).first?.string }
                    token.finish(lines.joined(separator: "\n"))
                }
                request.recognitionLevel = .accurate
                request.usesLanguageCorrection = false
                request.preferBackgroundProcessing = true
                // iOS 15 中文保持 accurate；语言配置沿用现有实测结构。
                request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
                token.attach(request)
                guard !token.isCancelled else { return }

                let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
                do {
                    try handler.perform([request])
                } catch {
                    token.finish("")
                }
            }
        }
        return token
    }
}
