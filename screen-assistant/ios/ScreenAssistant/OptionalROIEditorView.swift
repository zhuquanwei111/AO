import SwiftUI

/// 新数据源ROI只有显式保存后才启用；未配置时从空白画布拖出区域，不猜主播界面坐标。
struct OptionalROIEditorView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var selection: CGRect
    @State private var didEdit = false

    let title: String
    let onSave: (CGRect) -> Void

    init(title: String, currentROI: CGRect?, onSave: @escaping (CGRect) -> Void) {
        self.title = title
        self.onSave = onSave
        _selection = State(initialValue: currentROI ?? .zero)
    }

    var body: some View {
        VStack(spacing: 12) {
            Text(selection.isEmpty
                 ? "请在竖屏参照上从一角拖到另一角，框选主播界面中的目标区域；保存后才启用采集。"
                 : "请按主播界面拖动并缩放到目标区域；保存后才启用采集。")
                .font(.footnote)
                .foregroundColor(.secondary)
                .padding(.horizontal)
            ROIEditorView(roi: $selection, title: title, showsPresets: false,
                          allowsNewSelection: true)
                .onChange(of: selection) { _ in didEdit = true }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button("保存并启用") {
                    onSave(selection)
                    dismiss()
                }
                .disabled(!didEdit || selection.isEmpty)
            }
        }
    }
}
