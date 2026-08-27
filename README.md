# Raspberry Pi 延时摄影控制系统

面向 Raspberry Pi + Camera Module 3 的单机延时摄影工具。浏览器可查看画面、创建和切换天空/植物/日常起居方案、控制拍摄、自定义素材存储位置，并将当前项目一键导出为 MP4。

## 功能

- 七套专业预设：天空云层、黄金时段、室内植物、生长季、起居室一天、室内活动、一周起居
- 锁定对焦、曝光、增益和白平衡，避免延时闪烁与焦点呼吸
- `always`、钟点、日出日落和手动事件四类拍摄窗口
- 开始、暂停、继续、停止、测试拍摄和一次自动对焦
- 项目级自定义绝对存储路径，按日期保存 JPEG，低空间自动停拍
- 响应式 Web 控制台，可通过手机或电脑访问
- 后台 ffmpeg 合成 H.264 MP4，完成后一键下载

## 系统要求

- Raspberry Pi 4（4 GB+）或 Pi 5
- Raspberry Pi OS Bookworm 64-bit
- Official Camera Module 3 Standard 或 Wide
- 推荐使用挂载在 `/mnt/ssd` 的 SSD 保存素材

## 安装

先确认排线方向正确，并更新系统：

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-libcamera python3-venv ffmpeg git
rpicam-hello --list-cameras
```

Picamera2 来自系统 apt 包。为使虚拟环境能够读取该包，创建环境时必须带 `--system-site-packages`：

```bash
cd /opt
sudo git clone <你的仓库地址> pi-timelapse
sudo chown -R "$USER":"$USER" /opt/pi-timelapse
cd /opt/pi-timelapse
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
.venv/bin/timelapse check-camera
```

开发或首次校准时可直接启动：

```bash
mkdir -p "$HOME/.local/share/pi-timelapse"
.venv/bin/timelapse serve \
  --host 0.0.0.0 \
  --port 8080 \
  --state-dir "$HOME/.local/share/pi-timelapse"
```

同一局域网的浏览器访问 `http://树莓派IP:8080`。可用 `hostname -I` 查看地址。

## SSD 与存储位置

推荐用 UUID 固定挂载，避免设备名变化：

```bash
lsblk -f
sudo mkdir -p /mnt/ssd
sudo chown timelapse:timelapse /mnt/ssd
```

在 `/etc/fstab` 添加对应 UUID 后执行 `sudo mount -a`。Web 创建项目时填写如 `/mnt/ssd/timelapse`。系统会检查路径为绝对路径、可写且空间达到项目阈值；项目建立后不会自动搬迁已有素材。

素材结构：

```text
/mnt/ssd/timelapse/<project_id>/
  project.yaml
  state.json
  frames/2026-08-23/frame_20260823_090102_123.jpg
  previews/
  exports/<project_id>_20260823_230000.mp4
  logs/
```

## 配置 systemd 自启动

创建服务用户并授权相机、状态目录和素材盘：

```bash
sudo useradd --system --home /var/lib/pi-timelapse --create-home timelapse
sudo usermod -aG video timelapse
sudo chown -R timelapse:timelapse /var/lib/pi-timelapse /mnt/ssd /opt/pi-timelapse
sudo cp systemd/timelapse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now timelapse
sudo systemctl status timelapse
```

查看日志：

```bash
journalctl -u timelapse -f
```

若项目使用 `/media`、NAS 或其他位置，需要保证 `timelapse` 用户对该路径有读写权限，并确保挂载点先于服务可用。

## 使用流程

1. 打开 Web 控制台，点击“新建项目”。
2. 选择方案，填写项目名称、ID 和树莓派上的存储位置。
3. 创建后先“测试拍摄”，检查构图和曝光。
4. 点击“自动对焦并锁定”，确认焦点后开始拍摄。
5. 日常起居有访客或涉及隐私时点击“暂停”；恢复不会追赶漏拍。
6. 项目有照片后点击“生成视频”。编码在后台进行，结束后点击“下载 MP4”。
7. 切换项目会先安全停止当前拍摄，系统不会并发占用同一相机。

默认只服务局域网且未内置公网认证。不要直接做路由器端口映射；远程访问请使用 Tailscale 等私有网络。

## 测试

开发机可测试不依赖相机的配置和调度逻辑：

```bash
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

树莓派验收：

1. `timelapse check-camera` 能列出 IMX708。
2. 分别创建 `sky`、`grow`、`life` 项目并完成测试拍摄。
3. 用短间隔拍摄 20 张，验证暂停/继续不会补拍。
4. 切换项目，确认旧任务已停止且新项目目录独立。
5. 拔插 SSD 或把空间阈值调高，确认系统进入错误状态且不再写帧。
6. 导出 MP4 并在手机浏览器下载播放。
7. 拍摄中重启服务，确认已有帧不被覆盖。
