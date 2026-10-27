import CoreGraphics
import Foundation
import Vision

/// 无历史的Vision适配。像素只在本次单在途作业内使用，返回小数值摘要。
enum PersonVisionFeatures {
    struct Result {
        var gray: [Double] = []
        var quality = "unknown"
        var count: Int?
        var confidence: Double?
        var box: [Double]?
        var body: [Double]?
        var face: [Double]?
        var headRatio: Double?
        var headConfidence: Double?
    }

    static func inspect(_ image: CGImage, previous: [Double]?) -> Result {
        var output = Result()
        guard image.width >= 96, image.height >= 96, let gray = luminances(image),
              let evidence = ImageDiagnosticEvidence(luminances: gray, previous: previous,
                                                     faceCount: nil, at: 0) else { return output }
        output.gray = gray
        if evidence.isNearBlack { output.quality = "near_black"; return output }
        if evidence.contrast < 0.035 { output.quality = "low_detail"; return output }
        let edge = zip(gray.dropFirst(), gray).reduce(0.0) { $0 + abs($1.0 - $1.1) } / Double(gray.count - 1)
        if edge < 0.008 { output.quality = "low_detail"; return output }
        guard let change = evidence.meanChange else { return output }
        if change < 0.0008 { output.quality = "frozen_or_still"; return output }
        if change > 0.25 { output.quality = "scene_change"; return output }
        let pose = VNDetectHumanBodyPoseRequest()
        let faces = VNDetectFaceLandmarksRequest()
        let people = VNDetectHumanRectanglesRequest()
        people.upperBodyOnly = true
        do {
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([pose, faces, people])
        } catch { output.quality = "vision_error"; return output }
        let poses = pose.results ?? []
        let faceResults = faces.results ?? []
        let rectangles = people.results ?? []
        let count = max(poses.count, max(faceResults.count, rectangles.count))
        output.count = min(count, 10)
        guard count <= 1 else { output.quality = "multiple_people"; return output }
        if count == 0 { output.quality = "valid"; return output }
        let confidence = max(poses.first?.confidence ?? 0,
                             max(faceResults.first?.confidence ?? 0, rectangles.first?.confidence ?? 0))
        output.confidence = Double(confidence)
        guard confidence >= 0.65 else { output.quality = "low_confidence"; return output }
        output.quality = "valid"
        if let person = rectangles.first, person.confidence >= 0.65 {
            let b = person.boundingBox
            output.box = [Double(b.minX), Double(1 - b.maxY), Double(b.width), Double(b.height)]
        }
        if let pose = poses.first,
           let nose = try? pose.recognizedPoint(.nose),
           let left = try? pose.recognizedPoint(.leftShoulder),
           let right = try? pose.recognizedPoint(.rightShoulder),
           nose.confidence >= 0.45, left.confidence >= 0.45, right.confidence >= 0.45 {
            let width = hypot(left.location.x - right.location.x, left.location.y - right.location.y)
            if width >= 0.08 {
                let x = (left.location.x + right.location.x) / 2
                let y = (left.location.y + right.location.y) / 2
                var vector = [nose.location, left.location, right.location].flatMap {
                    [Double(($0.x - x) / width), Double(($0.y - y) / width)]
                }
                if vector.allSatisfy({ $0.isFinite && (-5...5).contains($0) }) {
                    output.body = vector
                    output.headRatio = vector[1]
                    output.headConfidence = Double(min(nose.confidence, min(left.confidence, right.confidence)))
                    if let leftWrist = try? pose.recognizedPoint(.leftWrist),
                       let rightWrist = try? pose.recognizedPoint(.rightWrist),
                       leftWrist.confidence >= 0.45, rightWrist.confidence >= 0.45 {
                        let wrists = [leftWrist.location, rightWrist.location].flatMap {
                            [Double(($0.x - x) / width), Double(($0.y - y) / width)]
                        }
                        if wrists.allSatisfy({ $0.isFinite && (-5...5).contains($0) }) {
                            vector.append(contentsOf: wrists)
                            output.body = vector
                        }
                    }
                }
            }
        }
        // 只保留正面、足够大的人脸眼口几何。不能解释成笑、疲惫或情绪。
        if let face = faceResults.first, face.confidence >= 0.65,
           Double(face.boundingBox.width) * Double(image.width) >= 80,
           let yaw = face.yaw?.doubleValue, abs(yaw) < 0.35,
           let roll = face.roll?.doubleValue, abs(roll) < 0.25,
           let landmarks = face.landmarks,
           let left = aperture(landmarks.leftEye, aspect: face.boundingBox.size),
           let right = aperture(landmarks.rightEye, aspect: face.boundingBox.size),
           let mouth = aperture(landmarks.outerLips, aspect: face.boundingBox.size) {
            output.face = [left, right, mouth]
        }
        return output
    }

    private static func aperture(_ region: VNFaceLandmarkRegion2D?, aspect: CGSize) -> Double? {
        guard let region, region.pointCount >= 4 else { return nil }
        let points = (0..<region.pointCount).map { region.normalizedPoints[$0] }
        guard let minX = points.map({ $0.x }).min(), let maxX = points.map({ $0.x }).max(),
              let minY = points.map({ $0.y }).min(), let maxY = points.map({ $0.y }).max() else { return nil }
        let width = Double(maxX - minX) * Double(aspect.width)
        guard width > 0.001 else { return nil }
        let value = Double(maxY - minY) * Double(aspect.height) / width
        return value.isFinite && (0...2).contains(value) ? value : nil
    }

    private static func luminances(_ image: CGImage) -> [Double]? {
        var bytes = [UInt8](repeating: 0, count: 256)
        let success = bytes.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: 16, height: 16,
                                          bitsPerComponent: 8, bytesPerRow: 16,
                                          space: CGColorSpaceCreateDeviceGray(),
                                          bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: 16, height: 16))
            return true
        }
        return success ? bytes.map { Double($0) / 255 } : nil
    }
}
