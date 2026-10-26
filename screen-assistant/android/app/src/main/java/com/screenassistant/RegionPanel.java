package com.screenassistant;

import android.app.Activity;
import android.app.AlertDialog;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.ArrayAdapter;
import android.widget.TextView;

final class RegionPanel {
    private final Activity activity;
    private final Spinner selection;
    private final TextView state;
    RegionPanel(Activity activity, LinearLayout parent) {
        this.activity = activity;
        selection = new Spinner(activity);
        selection.setAdapter(new ArrayAdapter<>(activity, android.R.layout.simple_spinner_dropdown_item, Regions.LABELS));
        parent.addView(selection);
        state = new TextView(activity); parent.addView(state);
        Button clear = new Button(activity); clear.setText("清除所选区域"); parent.addView(clear);
        clear.setOnClickListener(v -> { Prefs.clearRegion(activity, selected()); refresh(); });
        CheckBox online = new CheckBox(activity); online.setText("已核对人数框是当前在线人数（非热度/累计观看）");
        online.setChecked(Prefs.onlineConfirmed(activity)); parent.addView(online);
        online.setOnCheckedChangeListener((v, checked) -> Prefs.setOnlineConfirmed(activity, checked));
        Button preset = new Button(activity); preset.setText("应用参考竖屏直播区域"); parent.addView(preset);
        preset.setOnClickListener(v -> new AlertDialog.Builder(activity).setMessage("覆盖公屏、在线、人物三个区域；请按主播侧实际画面调整。平台通知保持当前配置。")
                .setPositiveButton("应用", (d, which) -> {
                    Prefs.setRegion(activity, "comment", .03f, .69f, .85f, .94f);
                    Prefs.setRegion(activity, "online_count", .80f, .055f, .90f, .10f);
                    Prefs.setRegion(activity, "person", 0f, .14f, 1f, .94f);
                    Prefs.setOnlineConfirmed(activity, false); online.setChecked(false); refresh();
                }).setNegativeButton("取消", null).show());
        refresh();
    }
    String selected() { return Regions.KEYS[selection.getSelectedItemPosition()]; }
    void refresh() {
        StringBuilder s = new StringBuilder("固定区域（左上原点）\n");
        for (int i = 1; i < Regions.KEYS.length; i++) s.append(Regions.LABELS[i]).append("：")
                .append(Prefs.getRegion(activity, Regions.KEYS[i]) == null ? "未配置" : "已配置").append("\n");
        state.setText(s);
    }
}
