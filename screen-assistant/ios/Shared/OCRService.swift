import Vision
import CoreGraphics
import Foundation

/// Vision 中文 OCR 服务（架构无关组件）。
/// 当前由广播扩展调用；若主通道切换为“哑扩展 + shm/UDS 帧通道 + 主 App OCR”
/// （R/36 建议，待真机冒烟验证），本模块可原样移至主 App 调用，无需重写。
enum OCRService {
    /// 对裁剪后的 CGImage 执行中文 OCR，按近似阅读顺序返回行拼接文本。
    /// 识别文本仅存在于闭包链内存，不保存。
    ///
    /// 平台事实：iOS 15 的 Vision `.fast` 档不支持中文（仅拉丁），
    /// `.accurate` 档支持 zh-Hans/zh-Hant（R/36 核实）；accurate 档对中文
    /// 按“行/行片段”切分（官方文档），此处逐行取 topCandidate 后拼接。
    static func recognizeText(in cgImage: CGImage,
                              completion: @escaping (String) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            autoreleasepool {
                let request = VNRecognizeTextRequest { request, error in
                    guard error == nil,
                          let observations = request.results as? [VNRecognizedTextObservation] else {
                        completion("")
                        return
                    }
                    // Vision 不保证返回阅读顺序；按 boundingBox 近似排序
                    //（自上而下、同行自左向右；boundingBox 为左下原点归一化坐标）。
                    let sorted = observations.sorted { a, b in
                        let dy = abs(a.boundingBox.minY - b.boundingBox.minY)
                        if dy > 0.01 { return a.boundingBox.minY > b.boundingBox.minY }
                        return a.boundingBox.minX < b.boundingBox.minX
                    }
                    let lines = sorted.compactMap { $0.topCandidates(1).first?.string }
                    completion(lines.joined(separator: "\n"))
                }
                request.recognitionLevel = .accurate
                request.usesLanguageCorrection = false
                request.preferBackgroundProcessing = true
                // 官方：简/繁中文识别语言为 zh-Hans / zh-Hant，须为前两个元素，
                // 英语是唯一可搭配语言。
                request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]

                let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
                do {
                    try handler.perform([request])
                } catch {
                    completion("")
                }
            }
        }
    }
}
