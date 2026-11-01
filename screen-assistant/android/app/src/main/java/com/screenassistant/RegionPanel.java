package com.screenassistant;

import android.app.Activity;
import android.app.AlertDialog;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.TextView;

final class RegionPanel {
    private final Activity activity;
    private final TextView state;
    private final CheckBox online;
    RegionPanel(Activity activity, LinearLayout parent) {
        this.activity = activity;
        state = new TextView(activity); parent.addView(state);
        online = new CheckBox(activity); online.setText("已核对人数框是当前在线人数（非热度/累计观看）");
        online.setChecked(Prefs.onlineConfirmed(activity)); parent.addView(online);
        online.setOnCheckedChangeListener((v, checked) -> Prefs.setOnlineConfirmed(activity, checked));
        Button preset = new Button(activity); preset.setText("恢复评论和人数参考区域"); parent.addView(preset);
        preset.setOnClickListener(v -> new AlertDialog.Builder(activity).setMessage("将覆盖已保存的评论和在线人数区域，其他历史区域保留。参考位置需要在实际直播画面中校准。")
                .setPositiveButton("应用", (d, which) -> {
                    for (int i=0;i<Regions.ACTIVE_KEYS.length;i++) {
                        float[] r=i==0 ? Regions.commentDefault(.948f) : Regions.onlineDefault();
                        Prefs.setRegion(activity,Regions.ACTIVE_KEYS[i],r[0],r[1],r[2],r[3]);
                    }
                    Prefs.setOnlineConfirmed(activity, false); online.setChecked(false); refresh();
                }).setNegativeButton("取消", null).show());
        refresh();
    }
    void refresh() {
        online.setChecked(Prefs.onlineConfirmed(activity));
        StringBuilder s = new StringBuilder("当前只采集以下区域；编辑页可同时查看和调整。\n");
        for (int i = 0; i < Regions.ACTIVE_KEYS.length; i++) s.append(Regions.ACTIVE_LABELS[i]).append("：")
                .append(Prefs.getRegion(activity, Regions.ACTIVE_KEYS[i]) == null ? "未配置（编辑页显示参考位置）" : "已配置").append("\n");
        state.setText(s);
    }
}
