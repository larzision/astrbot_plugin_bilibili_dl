# B站视频下载助手 (astrbot_plugin_bili_dl)

AstrBot 的 B站视频下载插件。群里甩 B站链接、分享卡片、b23短链、BV号、av号，自动解析下载并发回群里。

作者：miko · 版本：1.0.0

## 功能

- 自动识别 B站链接、分享卡片、b23.tv 短链、BV号、av号
- 最高 1080p 画质，默认 720p，优先 H.264
- 支持纯音频模式（转 mp3）
- 短链先解重定向拿真BV，避免重复下载，不卡消息
- 超过大小上限的文件不发，避免刷屏失败
- 发完自动清理临时文件，可配置永久保存

## 安装

插件目录放入 AstrBot 的 `data/plugins/` 下，重启或在管理面板重载插件。

依赖：`imageio-ffmpeg`。yt-dlp 与 ffmpeg 已内置在插件 `bin/` 目录。

## 使用

默认自动触发，直接在群里发：

- `https://www.bilibili.com/video/BVxxxx`
- B站分享卡片
- `https://b23.tv/xxxx`
- 纯 `BV1xxxxxxxxx` 或 `av123456`

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| auto.enable | true | 自动识别B站链接并下载 |
| download.max_quality | 720p | 最高画质：480p / 720p / 1080p |
| download.max_size_mb | 100 | 超过此大小不发视频 |
| download.auto_delete_seconds | 90 | 临时文件多久后自动删除（秒） |
| download.prefer_h264 | true | 优先 H.264 编码 |
| download.mode | video | video=完整视频，audio=只下音频(mp3) |
| download.save_dir | 空 | 留空=插件数据目录(发完自动删)，填写路径=永久保存 |
| download.save_file | false | 开=保留文件，关=发完自动删除 |

## 数据目录

- 运行数据默认存于 `data/plugin_data/astrbot_plugin_bili_dl/`
- 配置了 `save_dir` 时，文件保存到指定路径（如 Android 的 `/storage/emulated/0/Download/bili_music/`）

## 注意事项

- 超过 `max_size_mb` 的文件不会发到群里，保留在下载目录，按 `auto_delete_seconds` 清理。
- 1080p 以上画质或部分视频需要 B站 登录 cookie（SESSDATA），未配置时可能降级或失败。
