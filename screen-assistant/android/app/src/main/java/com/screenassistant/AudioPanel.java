package com.screenassistant;

import android.app.Activity;
import android.widget.ArrayAdapter;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.TextView;

final class AudioPanel {
    private final Spinner mode;
    private final CheckBox host;
    AudioPanel(Activity activity,LinearLayout parent) {
        TextView note=new TextView(activity);
        note.setText("音频只用于运营场次。普通App与抖音不能同时获得麦克风；播放捕获受抖音策略限制，也不等于主播上行声。默认关闭，权限失败仍继续屏幕取数。");
        parent.addView(note);
        mode=new Spinner(activity);
        mode.setAdapter(new ArrayAdapter<>(activity,android.R.layout.simple_spinner_dropdown_item,
                new String[]{"关闭音频","仅尝试抖音播放捕获","麦克风（同机开播可能受限）"}));
        mode.setSelection(Prefs.getAudioMode(activity)); parent.addView(mode);
        host=new CheckBox(activity); host.setText("已实际核对选定音轨为本场主播声音"); parent.addView(host);
        Prefs.setHostConfirmed(activity,false);
    }
    void save(Activity activity) { Prefs.setAudioMode(activity,mode.getSelectedItemPosition()); Prefs.setHostConfirmed(activity,host.isChecked()); }
    void refresh(boolean enabled) { mode.setEnabled(enabled); host.setEnabled(enabled); }
}
