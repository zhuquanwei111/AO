import SwiftUI

/// 归一化 ROI 编辑器：在竖屏满屏参照上拖动/缩放矩形。
/// 坐标为归一化值 (0..1)，**左上原点**——与 SharedConfig.roi() 一致，
/// 扩展侧换算为像素（CIImage 左下原点）后裁剪。
struct ROIEditorView: View {
    @Binding var roi: CGRect
    var title: String = "编辑识别区域"
    @State private var dragStart: CGRect?

    var body: some View {
        GeometryReader { proxy in
            // 以 390×844（iPhone 竖屏）比例拟合可用区域，
            // 保证编辑器的归一化比例与真实屏幕一致。
            let box = Self.fittedBox(available: proxy.size)
            let w = box.width
            let h = box.height

            ZStack {
                // 参照背景：模拟竖屏满屏
                Rectangle()
                    .fill(Color(.systemGray6))
                    .frame(width: w, height: h)

                // 框外暗化
                Path { p in
                    p.addRect(CGRect(origin: .zero, size: box))
                    p.addRect(pixelRect(w: w, h: h))
                }
                .fill(Color.black.opacity(0.35), style: FillStyle(eoFill: true))

                // ROI 矩形
                Rectangle()
                    .stroke(Color.accentColor, lineWidth: 2)
                    .frame(width: pixelRect(w: w, h: h).width,
                           height: pixelRect(w: w, h: h).height)
                    .position(x: pixelRect(w: w, h: h).midX,
                              y: pixelRect(w: w, h: h).midY)
                    .gesture(
                        DragGesture()
                            .onChanged { value in
                                if dragStart == nil { dragStart = roi }
                                guard let start = dragStart else { return }
                                let dx = value.translation.width / w
                                let dy = value.translation.height / h
                                var r = start
                                let newX = min(max(r.minX + dx, 0), 1 - r.width)
                                r.origin.x = newX
                                let newY = min(max(r.minY + dy, 0), 1 - r.height)
                                r.origin.y = newY
                                roi = r
                            }
                            .onEnded { _ in dragStart = nil }
                    )
                    .contentShape(Rectangle())

                // 右下角缩放手柄
                Circle()
                    .fill(Color.accentColor)
                    .frame(width: 22, height: 22)
                    .position(x: pixelRect(w: w, h: h).maxX,
                              y: pixelRect(w: w, h: h).maxY)
                    .gesture(
                        DragGesture()
                            .onChanged { value in
                                if dragStart == nil { dragStart = roi }
                                guard let start = dragStart else { return }
                                let dx = value.translation.width / w
                                let dy = value.translation.height / h
                                var r = start
                                r.size.width = min(max(start.width + dx, 0.05), 1 - start.minX)
                                r.size.height = min(max(start.height + dy, 0.05), 1 - start.minY)
                                roi = r
                            }
                            .onEnded { _ in dragStart = nil }
                    )
            }
            .frame(width: box.width, height: box.height)
            .position(x: proxy.size.width / 2, y: proxy.size.height / 2)
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Menu {
                    Button("整屏") { roi = CGRect(x: 0.05, y: 0.05, width: 0.90, height: 0.90) }
                    Button("上半屏") { roi = CGRect(x: 0.05, y: 0.05, width: 0.90, height: 0.42) }
                    Button("下半屏") { roi = CGRect(x: 0.05, y: 0.53, width: 0.90, height: 0.42) }
                    Button("中部横条（字幕区）") { roi = CGRect(x: 0.05, y: 0.72, width: 0.90, height: 0.20) }
                    Button("左上四分之一") { roi = CGRect(x: 0.05, y: 0.05, width: 0.42, height: 0.42) }
                } label: {
                    Text("预设")
                }
            }
        }
    }

    /// 以竖屏满屏参照（iPhone 主流 390×844，比例 19.5:9 ≈ 0.462）在可用区域内居中拟合。
    private static func fittedBox(available: CGSize) -> CGSize {
        let referenceRatio: CGFloat = 390 / 844
        let availableRatio = available.width / max(available.height, 1)
        if availableRatio > referenceRatio {
            // 高度受限
            let h = available.height
            return CGSize(width: h * referenceRatio, height: h)
        } else {
            let w = available.width
            return CGSize(width: w, height: w / referenceRatio)
        }
    }

    /// 归一化 ROI → 视图像素（左上原点）。
    private func pixelRect(w: CGFloat, h: CGFloat) -> CGRect {
        CGRect(x: roi.minX * w,
               y: roi.minY * h,
               width: roi.width * w,
               height: roi.height * h)
    }
}
