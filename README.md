# 时光延时

面向 **Raspberry Pi + 官方 Camera Module 3** 的单机延时摄影系统。在局域网浏览器里创建项目、看实时画面、锁定相机参数、按间隔拍 JPEG，再用 ffmpeg 合成 H.264 MP4。

界面名称：**时光延时**。控制台默认地址：`http://<树莓派IP或主机名>:8080/`。

## 当前版本

包版本 **0.1.0**。拍摄、预览、调参、相册、导出均在同一 Web 服务中完成。详细功能见 [项目说明文档.md](项目说明文档.md)。

---

## 能做什么

- **15 套拍摄方案**（天空 / 植物 / 日常与街景），创建项目时写入该项目的相机与间隔配置  
- 每个项目独立存储目录；可切换项目，同时只占用一颗相机  
- **开始 / 暂停 / 继续 / 停止**、定时开拍（可选自动结束）；暂停后不补拍漏掉的时间槽  
- 拍摄窗口：全天、钟点、日出日落、事件  
- 项目级相机设置：对焦、曝光、白平衡、分辨率、JPEG 质量、**180° 旋转**（倒装）  
- 空闲时实时预览；拍摄中显示最新已保存帧  
- 相册筛选与删除；导出 24 / 25 / 30 fps，可选时间戳或文本水印  
- 磁盘空间不足自动停拍；服务重启后尽量恢复正在进行的拍摄  

更细的功能说明、方案列表和使用注意见 **[项目说明文档.md](项目说明文档.md)**。

---

## 硬件与系统

| 项目 | 要求 |
| --- | --- |
| 主机 | Raspberry Pi 4（建议 4 GB+）或 Pi 5 |
| 系统 | Raspberry Pi OS Bookworm 64-bit |
| 相机 | 官方 Camera Module 3（Standard 或 Wide，IMX708） |
| 排线 | Pi 4 用 15 针 CSI；Pi 5 用 22 针专用相机排线 |
| 存储 | 系统用 SD 卡；**原图建议写 USB SSD**（长期项目需要大容量） |
| 电源 | 官方电源 |

不支持把 USB 网络摄像头当作正式方案。完整选型、挂盘、systemd / Docker 与排错见 **[部署配置指南.md](部署配置指南.md)**。

---

## 快速开始

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-libcamera python3-venv ffmpeg git
rpicam-hello --list-cameras
```

应能看到 **imx708**。Picamera2 必须用系统 apt 包；虚拟环境要加 `--system-site-packages`：

```bash
sudo mkdir -p /opt/pi-timelapse
sudo chown -R "$USER":"$USER" /opt/pi-timelapse
# 将本仓库放到 /opt/pi-timelapse 后：
cd /opt/pi-timelapse
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
.venv/bin/timelapse check-camera
```

前台试跑：

```bash
sudo mkdir -p /var/lib/pi-timelapse
.venv/bin/timelapse serve --host 0.0.0.0 --port 8080 --state-dir /var/lib/pi-timelapse
```

浏览器打开 `http://<树莓派IP>:8080/`。长期运行二选一：

- **原生 systemd**：`systemd/timelapse.service`（用户 `pi`，目录 `/opt/pi-timelapse`），见部署指南第 6 节。  
- **Docker**：`docker compose up -d --build`，或 `timelapse-docker.service`，见部署指南第 6.1 节。不要与原生服务同时开。

创建项目时填写树莓派上的**绝对路径**作为素材根目录（例如 `/mnt/ssd/timelapse`）。控制台**没有登录认证**，只应放在局域网；不要做公网端口映射。

---

## 仓库结构

```text
config/presets/          15 套拍摄方案 YAML
src/timelapse/           应用代码（相机、调度、存储、导出、Web）
src/timelapse/web/       控制台页面与 API
systemd/                 原生 timelapse.service 与可选 timelapse-docker.service
Dockerfile               树莓派 arm64 镜像（含 Picamera2 系统包）
docker-compose.yml       相机设备、/mnt /media、状态目录
docker/entrypoint.sh
tests/                   不依赖真机相机的测试
项目说明文档.md
部署配置指南.md
docs/                    拍摄设计（与成品对照）与当前技术实现
```

命令行入口：`timelapse serve`、`timelapse check-camera`。

---

## 使用流程（摘要）

1. 新建项目，选择方案，填写名称、ID 和存储路径。  
2. 看实时画面；在「相机设置」里对焦、曝光、必要时旋转 180°，再写入当前项目。  
3. 开始拍摄，或预约开始时间。有人进出或涉及隐私可暂停。  
4. 在相册里检查照片；需要时「生成视频」并下载 MP4。  
5. 切换项目会先停止当前拍摄。

---

## 测试

开发机（无需相机）可跑配置与调度测试：

```bash
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

真机验收：`check-camera` 能列出模组；短间隔拍若干张；暂停不补拍；导出能播放；重启服务后已有帧不被覆盖。细节见部署指南。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [项目说明文档.md](项目说明文档.md) | 功能、15 套方案、使用注意 |
| [部署配置指南.md](部署配置指南.md) | 硬件、安装、挂盘、systemd、Docker、更新与排错 |
| [docs/01-延时摄影设计方案.md](docs/01-延时摄影设计方案.md) | 拍摄原则，并对照当前成品 |
| [docs/02-技术实现方案.md](docs/02-技术实现方案.md) | 与代码一致的架构、配置、API、调度与导出 |

包版本 `0.1.0`（`pyproject.toml`）。控制与拍摄以 Web 为主；CLI 为 `timelapse serve` 与 `timelapse check-camera`（Docker 入口也可直接 `check-camera`）。
