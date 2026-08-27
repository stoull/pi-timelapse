from __future__ import annotations

from typing import Any

# Camera Module 3 (IMX708) via Picamera2 / libcamera.
# Aperture is fixed hardware; digital zoom (ScalerCrop) is omitted for now.
CONTROL_SPEC: dict[str, Any] = {
    "hardware": "Raspberry Pi Camera Module 3 (IMX708)",
    "notes": [
        "光圈不可调（模组固定光圈）。",
        "LensPosition 单位为屈光度：0 为无限远，数值越大对焦越近。",
        "快门单位为微秒；手动曝光时需同时关闭自动曝光。",
        "延时摄影建议锁定对焦与白平衡，避免闪烁。",
    ],
    "fields": {
        "rotation": {
            "label": "画面旋转",
            "options": [
                {"value": 0, "label": "不旋转"},
                {"value": 180, "label": "旋转 180°"},
            ],
            "hint": "相机倒装时选 180°；切换时会短暂重启相机",
        },
        "af_mode": {
            "label": "对焦模式",
            "options": [
                {"value": "auto_once", "label": "单次自动对焦"},
                {"value": "manual", "label": "手动对焦"},
            ],
        },
        "lens_position": {
            "label": "焦距（屈光度）",
            "min": 0,
            "max": 15,
            "step": 0.05,
            "hint": "0=无限远，约 10≈10cm",
        },
        "af_range": {
            "label": "对焦范围",
            "options": [
                {"value": "normal", "label": "常规"},
                {"value": "macro", "label": "微距"},
                {"value": "full", "label": "全范围"},
            ],
        },
        "af_speed": {
            "label": "对焦速度",
            "options": [
                {"value": "normal", "label": "正常"},
                {"value": "fast", "label": "快速"},
            ],
        },
        "ae_enable": {"label": "自动曝光", "type": "bool"},
        "exposure_time_us": {
            "label": "快门（微秒）",
            "min": 100,
            "max": 2_000_000,
            "step": 100,
            "hint": "1000000=1秒；33333≈1/30秒",
        },
        "analogue_gain": {
            "label": "模拟增益",
            "min": 1,
            "max": 16,
            "step": 0.1,
        },
        "exposure_value": {
            "label": "曝光补偿 EV",
            "min": -4,
            "max": 4,
            "step": 0.125,
            "hint": "仅自动曝光时生效",
        },
        "awb_enable": {"label": "自动白平衡", "type": "bool"},
        "awb_mode": {
            "label": "白平衡预设",
            "options": [
                {"value": "auto", "label": "自动"},
                {"value": "tungsten", "label": "钨丝灯"},
                {"value": "fluorescent", "label": "荧光灯"},
                {"value": "indoor", "label": "室内"},
                {"value": "daylight", "label": "日光"},
                {"value": "cloudy", "label": "阴天"},
            ],
        },
        "colour_gain_r": {
            "label": "红色增益",
            "min": 0.5,
            "max": 8,
            "step": 0.05,
        },
        "colour_gain_b": {
            "label": "蓝色增益",
            "min": 0.5,
            "max": 8,
            "step": 0.05,
        },
        "brightness": {"label": "亮度", "min": -1, "max": 1, "step": 0.05},
        "contrast": {"label": "对比度", "min": 0, "max": 4, "step": 0.05},
        "saturation": {"label": "饱和度", "min": 0, "max": 4, "step": 0.05},
        "sharpness": {"label": "锐度", "min": 0, "max": 8, "step": 0.1},
        "hdr": {"label": "HDR", "type": "bool", "hint": "部分系统需重启相机后生效"},
        "jpeg_quality": {"label": "JPEG 质量", "min": 70, "max": 100, "step": 1},
        "main_size": {
            "label": "照片分辨率",
            "options": [
                {"value": "1920x1080", "label": "1920 × 1080"},
                {"value": "2304x1296", "label": "2304 × 1296"},
                {"value": "3840x2160", "label": "3840 × 2160"},
                {"value": "4608x2592", "label": "4608 × 2592（全幅，夜间对焦较慢）"},
            ],
        },
    },
}
