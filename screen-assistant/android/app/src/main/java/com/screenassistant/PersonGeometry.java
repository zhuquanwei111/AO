package com.screenassistant;

/** 将ML Kit点先归一化到ROI，再按现有iOS鼻/肩向量约定转成向上y。 */
final class PersonGeometry {
    static double[] body(double[] xy,int width,int height) {
        if (xy.length!=6 || width<=0 || height<=0) return null;
        double[] normalized=new double[6];
        for (int i=0;i<6;i++) {
            normalized[i]=xy[i]/(i%2==0?width:height);
            if (!Double.isFinite(normalized[i]) || normalized[i]<0 || normalized[i]>1) return null;
        }
        double shoulderWidth=Math.hypot(normalized[2]-normalized[4],normalized[3]-normalized[5]);
        if (shoulderWidth<.08) return null;
        double x=(normalized[2]+normalized[4])/2,y=(normalized[3]+normalized[5])/2;
        double[] vector=new double[6];
        for (int i=0;i<6;i+=2) {
            vector[i]=(normalized[i]-x)/shoulderWidth;
            vector[i+1]=(y-normalized[i+1])/shoulderWidth;
            if (Math.abs(vector[i])>5 || Math.abs(vector[i+1])>5) return null;
        }
        return vector;
    }
}
