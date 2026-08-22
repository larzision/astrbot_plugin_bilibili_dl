import asyncio
import logging
import os
import re
import time
import glob
import subprocess
import urllib.request

import sys
# 优先使用插件内置的静态 yt-dlp，避免环境版本变动影响
_builtin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
if _builtin_dir not in sys.path:
    sys.path.insert(0, _builtin_dir)

import yt_dlp
import imageio_ffmpeg
from astrbot.api.all import *
from astrbot.api.message_components import Video, File
from astrbot.core.log import LogManager
from astrbot.core.star.star_tools import StarTools

@register("bili_dl_plugin", "miko", "B站视频下载助手", "1.0.0")
class BiliDlPlugin(Star):
    def __init__(self, context: Context, config: dict, *args, **kwargs):
        super().__init__(context)
        self.logger = LogManager.get_plugin_logger("bili_dl_plugin")
        self.config = config
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.temp_dir = str(StarTools.get_data_dir())
        os.makedirs(self.temp_dir, exist_ok=True)
        import shutil
        self.ffmpeg_exe = ""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        for cand in (os.path.join(plugin_dir, "bin", "ffmpeg"),
                     shutil.which("ffmpeg")):
            if cand and os.path.exists(cand):
                self.ffmpeg_exe = cand
                break
        if not self.ffmpeg_exe:
            try:
                self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                self.ffmpeg_exe = "ffmpeg"
        self.max_quality = self.config.get("download", {}).get("max_quality", "720p")
        self.mode = self.config.get("download", {}).get("mode", "video")
        self.save_dir = self.config.get("download", {}).get("save_dir", "") or self.temp_dir
        self.audio_save_dir = self.config.get("download", {}).get("audio_save_dir", "") or self.temp_dir
        self.save_file = self.config.get("download", {}).get("save_file", False)
        self.save_dir_fallback = False
        self.audio_save_dir_fallback = False
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except Exception as e:
            self.logger.warning("保存路径配置错误({})，已回退到插件temp目录".format(self.save_dir))
            self.save_dir = self.temp_dir
            self.save_dir_fallback = True
        try:
            os.makedirs(self.audio_save_dir, exist_ok=True)
        except Exception as e:
            self.logger.warning("音频保存路径配置错误({})，已回退到插件temp目录".format(self.audio_save_dir))
            self.audio_save_dir = self.temp_dir
            self.audio_save_dir_fallback = True
        self.max_size_mb = self.config.get("download", {}).get("max_size_mb", 100)
        self.delete_seconds = self.config.get("download", {}).get("auto_delete_seconds", 90)
        self.auto_enable = self.config.get("auto", {}).get("enable", True)
        # .nomedia 防相册读取：开=在保存目录加空文件，关=检查并删除
        self.no_media = bool(self.config.get("download", {}).get("no_media", False))
        self._apply_nomedia()
        self._recent = {}
        self.logger.info("B站下载插件已加载")

    def _apply_nomedia(self):
        """按配置在保存目录添加/删除 .nomedia 空文件，防止手机相册读取"""
        dirs = []
        for d in (self.save_dir, self.audio_save_dir):
            if d and d not in dirs:
                dirs.append(d)
        for d in dirs:
            try:
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, ".nomedia")
                if self.no_media:
                    if not os.path.exists(p):
                        open(p, "w").close()
                        self.logger.info(f"已添加 .nomedia: {p}")
                else:
                    if os.path.exists(p):
                        os.remove(p)
                        self.logger.info(f"已删除 .nomedia: {p}")
            except Exception as e:
                self.logger.warning(f".nomedia 处理失败 {d}: {e}")

    def _cleanup_partial(self, save_dir, prefix):
        # 下载失败时清理该次任务的全部残留（含 .part/.ytdl/半成品）
        try:
            for p in glob.glob(os.path.join(save_dir, prefix + ".*")):
                os.remove(p)
        except Exception:
            pass

    def _sanitize_filename(self, name: str) -> str:
        if not name:
            return "video"
        name = re.sub(r'[\\/*?:"<>|]', '_', name)
        return name.replace('\n', ' ').replace('\r', '')[:80].strip()

    def _format_size(self, size_bytes):
        if size_bytes is None:
            return "未知"
        if size_bytes < 1024:
            return "{} B".format(size_bytes)
        elif size_bytes < 1024 ** 2:
            return "{:.2f} KB".format(size_bytes / 1024)
        elif size_bytes < 1024 ** 3:
            return "{:.2f} MB".format(size_bytes / 1024 ** 2)
        else:
            return "{:.2f} GB".format(size_bytes / 1024 ** 3)

    def _local_url(self, path):
        # OneBot 同机可直接读 Android 真实路径，避免 HTTP 不可达
        if path.startswith(("/storage/", "/sdcard/")):
            return "file://" + path
        return None

    BILI_PATTERNS = [
        r'https?:\\?/\\?/(?:www\.|m\.)?bilibili\.com\\?/video\\?/[A-Za-z0-9_]+',
        r'https?:\\?/\\?/b23\.tv\\?/[A-Za-z0-9]+',
        r'(?:^|\s)(BV[0-9A-Za-z]{10})(?:\s|$)',
        r'(?:^|\s)(av\d+)(?:\s|$)',
    ]

    async def _resolve_url(self, url: str) -> str:
        # b23 短链先解重定向拿真 BV，丢线程池避免卡事件循环
        if "b23.tv" not in url:
            return url

        def _sync():
            try:
                req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.geturl()
            except Exception:
                return url

        return await asyncio.get_running_loop().run_in_executor(None, _sync)

    def _extract_urls(self, text: str) -> list:
        urls = []
        for pat in self.BILI_PATTERNS:
            for m in re.finditer(pat, text):
                u = m.group(0).strip()
                u = u.replace("\\/", "/")
                if u.startswith("BV") or u.startswith("av"):
                    u = "https://www.bilibili.com/video/" + u
                if u not in urls:
                    urls.append(u)
        return urls

    def _collect_text(self, event: AstrMessageEvent) -> str:
        raw = event.message_str or ""
        try:
            from astrbot.api.message_components import Json
            for comp in event.message_obj.message:
                if isinstance(comp, Json):
                    data = getattr(comp, "data", "") or ""
                    if isinstance(data, (dict, list)):
                        import json as _json
                        data = _json.dumps(data, ensure_ascii=False)
                    raw = raw + "\n" + str(data)
        except Exception as e:
            self.logger.warning("_collect_text 解析异常: {}".format(e))
        return raw

    def _estimate_size(self, info) -> int:
        # 预估下载大小，拿不到就返回 None
        total = 0
        found = False
        for f in (info.get("requested_formats") or [info]):
            for k in ("filesize", "filesize_approx"):
                v = f.get(k)
                if v:
                    total += v
                    found = True
                    break
        return total if found else None

    def _get_format(self):
        prefer_h264 = self.config.get("download", {}).get("prefer_h264", True)
        h = int(re.sub(r'\D', '', str(self.max_quality)) or 720)
        if prefer_h264:
            return "bv*[height<={}][vcodec^=avc1]+ba/b[height<={}][vcodec^=avc1]/b[height<={}]/b".format(h, h, h)
        return "bv*[height<={}]+ba/b[height<={}]/b[height<={}]/b".format(h, h, h)

    async def _get_info(self, url, fmt=None):
        opts = {"quiet": True, "no_warnings": True, "nocheckcertificate": True, "noplaylist": True}
        if fmt:
            opts["format"] = fmt
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False))
        except Exception as e:
            self.logger.error("解析失败: {}".format(e))
            return None

    def _build_dl_cmd(self, url, tmpl, fmt, merge=True):
        # 独立子进程跑 yt-dlp，不占主进程线程池
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist", "--quiet", "--no-warnings",
            "-o", tmpl, "-f", fmt,
            "--socket-timeout", "30",
            "--retries", "10",
            "--fragment-retries", "10",
            "--file-access-retries", "10",
            "--concurrent-fragments", "4",
            "--continue",
            "--print", "after_move:filepath",
        ]
        if merge:
            cmd += ["--merge-output-format", "mp4", "--ffmpeg-location", self.ffmpeg_exe]
        cmd.append(url)
        env = dict(os.environ)
        env["PYTHONPATH"] = _builtin_dir
        return cmd, env

    async def _run_dl(self, cmd, env):
        # 子进程 + nice 降优先级，崩了也不连累主进程
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: os.nice(10))
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise Exception((err or out).decode("utf-8", "ignore").strip()[-500:])
        lines = out.decode("utf-8", "ignore").splitlines()
        path = next((l.strip() for l in reversed(lines) if l.strip()), None)
        if not path or not os.path.exists(path):
            raise Exception("下载失败，无输出文件")
        return path

    async def _download(self, url, tmpl, info=None):
        cmd, env = self._build_dl_cmd(url, tmpl, self._get_format(), merge=True)
        return await self._run_dl(cmd, env), None

    def _has_audio_video(self, path):
        # 校验输出文件音频视频轨齐全，缺一不可，防半成品混过发送
        import subprocess
        try:
            r = subprocess.run(
                [self.ffmpeg_exe, "-i", path], capture_output=True, timeout=120)
            out = (r.stderr or b"").decode("utf-8", "ignore")
            return "Audio:" in out and "Video:" in out
        except Exception:
            return True  # 探测失败不误删，交给发送环节兜底

    async def _handle(self, event: AstrMessageEvent, url: str):
        if self.save_dir_fallback:
            yield event.plain_result("路径错误，已回退至插件temp")
        self.logger.info("开始解析: {}".format(url))
        info = await self._get_info(url, self._get_format())
        if not info:
            self.logger.error("解析失败，链接无效或网络异常: {}".format(url))
            return
        title = info.get("title", "未知标题")
        dur = info.get("duration")
        dur_str = "{}:{}".format(int(dur) // 60, int(dur) % 60) if dur else "未知"
        est = self._estimate_size(info)
        if est and est > self.max_size_mb * 1024 * 1024:
            self.logger.warning("预估过大 ({}MB)，跳过下载: {}".format(self._format_size(est), title))
            return
        self.logger.info("开始下载: {} | {}".format(title, dur_str))

        ts = int(time.time())
        try:
            path, _ = await self._download(url, os.path.join(self.save_dir, "bili_{}.%(ext)s".format(ts)), info)
        except Exception as e:
            self.logger.error("下载失败: {}".format(e))
            self._cleanup_partial(self.save_dir, "bili_{}".format(ts))
            return

        if not os.path.exists(path):
            cands = glob.glob(os.path.join(self.save_dir, "bili_{}.*".format(ts)))
            if not cands:
                self.logger.error("下载失败，无输出文件: {}".format(url))
                return
            path = cands[0]

        fsize = os.path.getsize(path)
        furl = path
        safe = self._sanitize_filename(title)
        ext = os.path.splitext(path)[1]

        if not self._has_audio_video(path):
            self.logger.error("下载结果缺少音频流，已清理: {}".format(path))
            self._cleanup_partial(self.save_dir, "bili_{}".format(ts))
            return

        if fsize > self.max_size_mb * 1024 * 1024:
            self.logger.warning(
                "文件过大 ({}MB)，无法发送，保留在: {}".format(
                    self._format_size(fsize), furl))
        else:
            send_url = self._local_url(path) or furl
            try:
                yield event.chain_result([Video(file=send_url, url=send_url)])
                self.logger.info("发送成功: {}{}".format(safe, ext))
            except Exception as e:
                self.logger.error("发送失败: {}，文件保留在: {}".format(e, furl))

        if not self.save_file:
            async def _clean():
                await asyncio.sleep(self.delete_seconds + 30)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
            asyncio.create_task(_clean())

    async def terminate(self):
        """插件卸载"""
        self.logger.info("B站下载插件已卸载")

    @event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.auto_enable:
            return
        raw = self._collect_text(event)
        comps = []
        try:
            comps = [type(c).__name__ for c in (event.message_obj.message or [])]
        except Exception:
            pass
        if raw.startswith(("/", "!", "video ", "download ", "直链 ", "bili ")):
            return
        urls = self._extract_urls(raw)
        if not urls:
            return
        url = await self._resolve_url(urls[0])
        now = time.time()
        # 顺带清理过期条目，限制缓存最多20条
        for k in list(self._recent):
            if self._recent[k] < now - 20:
                del self._recent[k]
        if len(self._recent) >= 20:
            old = min(self._recent, key=self._recent.get)
            del self._recent[old]
        if self._recent.get(url, 0) > now - 20:
            return
        self._recent[url] = now
        mode_name = "音频" if self.mode == "audio" else "视频"
        self.logger.info("自动触发下载({}): {}".format(mode_name, url))
        handler = self._handle_audio if self.mode == "audio" else self._handle
        async for res in handler(event, url):
            yield res

    async def _download_audio(self, url, tmpl, info=None):
        cmd, env = self._build_dl_cmd(url, tmpl, "bestaudio/best", merge=False)
        return await self._run_dl(cmd, env), None

    async def _handle_audio(self, event: AstrMessageEvent, url: str):
        if self.audio_save_dir_fallback:
            yield event.plain_result("路径错误，已回退至插件temp")
        self.logger.info("开始解析音频: {}".format(url))
        info = await self._get_info(url, "bestaudio/best")
        if not info:
            self.logger.error("音频解析失败: {}".format(url))
            return
        title = info.get("title", "未知标题")
        est = self._estimate_size(info)
        if est and est > self.max_size_mb * 1024 * 1024:
            self.logger.warning("音频预估过大 ({}MB)，跳过下载: {}".format(self._format_size(est), title))
            return
        self.logger.info("开始下载音频: {}".format(title))
        ts = int(time.time())
        try:
            orig_path, _ = await self._download_audio(
                url, os.path.join(self.audio_save_dir, "bilia_{}.%(ext)s".format(ts)), info)
        except Exception as e:
            self.logger.error("音频下载失败: {}".format(e))
            self._cleanup_partial(self.audio_save_dir, "bilia_{}".format(ts))
            return
        mp3_path = os.path.splitext(orig_path)[0] + ".mp3"
        # 音频模式直接改后缀：m4a -> mp3，不额外转码
        if orig_path.lower().endswith(".m4a") and not os.path.exists(mp3_path):
            try:
                os.rename(orig_path, mp3_path)
                orig_path = mp3_path
            except Exception as e:
                self.logger.error("音频改名失败: {}".format(e))
        path = orig_path
        # 转码后清理残留的原始 m4a/webm，只留要发的那个
        for p in glob.glob(os.path.join(self.audio_save_dir, "bilia_{}.*".format(ts))):
            if p != path:
                try:
                    os.remove(p)
                except Exception:
                    pass
        if not os.path.exists(path):
            self.logger.error("音频下载失败，无输出文件: {}".format(url))
            return
        fsize = os.path.getsize(path)
        furl = path
        safe = self._sanitize_filename(title)
        ext = os.path.splitext(path)[1]
        if fsize > self.max_size_mb * 1024 * 1024:
            self.logger.warning(
                "音频文件过大 ({}MB)，无法发送，保留在: {}".format(
                    self._format_size(fsize), furl))
        else:
            send_url = self._local_url(path) or furl
            try:
                yield event.chain_result([File(name=safe + ext, file=send_url, url=send_url)])
                self.logger.info("音频发送成功: {}{}".format(safe, ext))
            except Exception as e:
                self.logger.error("音频发送失败: {}，文件保留在: {}".format(e, furl))
        if not self.save_file:
            async def _clean():
                await asyncio.sleep(self.delete_seconds + 30)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
            asyncio.create_task(_clean())

