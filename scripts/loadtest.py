#!/usr/bin/env python
"""Load tests for the bot's own machinery.

Deliberately hits no real service: the platform edge and the Telegram edge are
both stubbed (see loadfakes). Everything between them — rate limiter, pacers,
semaphores, cache, event log, temp handling, delivery — is the real code.

    python scripts/loadtest.py all
    python scripts/loadtest.py cold --concurrency 8
"""

import argparse
import asyncio
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fakes import FakeBot, FakeMessage, FakeNotifier, make_download, make_probe

from src.core.config import Config
from src.core.errors import ClipRejected, ClipUnavailable
from src.core.pacing import Pacer, PlatformPacer
from src.media import extract, fetch
from src.storage.cache import FileIdCache
from src.storage.ratelimit import RateLimiter
from src.storage.stats import EventLog
from src.telegram import delivery, handlers
from src.telegram.services import Services

async def sample_latency(url: str, stop: asyncio.Event, samples: list,
                         interval: float = 0.5) -> None:
    """Poll the co-hosted app so its latency under load can be compared."""
    import urllib.request
    while not stop.is_set():
        started = time.monotonic()
        try:
            urllib.request.urlopen(url, timeout=10).read(1)
            samples.append(time.monotonic() - started)
        except Exception:
            samples.append(float("nan"))
        await asyncio.sleep(interval)


PWA_URL = "http://127.0.0.1:3000/"
# ru_maxrss is kilobytes on Linux and bytes on macOS.
RSS_DIVISOR = 1024 if sys.platform.startswith("linux") else 1024 ** 2
STATUS = Path("/proc/self/status")


def peak_rss_mb() -> float:
    """High-water mark: it never goes down, so it shows the peak, not the now."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / RSS_DIVISOR


def rss_mb() -> float:
    """Current resident size. The soak test needs this, not the watermark —
    a high-water mark can never show memory being released."""
    try:
        for line in STATUS.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return peak_rss_mb()


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


def report(name, latencies, seconds, extra=""):
    rate = len(latencies) / seconds if seconds else 0
    print(f"  {name:<26} n={len(latencies):<5} {seconds:6.1f}s  "
          f"{rate:5.1f}/s  p50={percentile(latencies, .5):5.2f}s  "
          f"p95={percentile(latencies, .95):5.2f}s  {extra}")


class Rig:
    """A disposable bot: temp databases, configurable limits, stubbed edges."""

    def __init__(self, workdir: Path, **overrides):
        settings = dict(bot_token="x", max_concurrent_downloads=3,
                        rate_limit_per_hour=0, chat_send_interval_seconds=0,
                        platform_interval_seconds=0, data_dir=workdir)
        settings.update(overrides)
        self.config = Config(**settings)
        self.services = Services(
            cache=FileIdCache(workdir / "cache.db", 30),
            events=EventLog(workdir / "events.db", 90, "salt"),
            chat_pacer=Pacer(self.config.chat_send_interval_seconds),
            platform_pacer=PlatformPacer(self.config.platform_interval_seconds,
                                         self.config.platform_cooldown_seconds),
            notifier=FakeNotifier(),
        )
        self.slots = asyncio.Semaphore(self.config.max_concurrent_downloads)
        self.limiter = RateLimiter(self.config.rate_limit_per_hour)
        self.bot = FakeBot()

    async def send(self, url, chat_id=-100, user_id=1, through_handler=True):
        message = FakeMessage(chat_id, user_id, url)
        started = time.monotonic()
        if through_handler:
            await handlers.handle_possible_link(
                message, self.bot, self.config, self.services, self.slots, self.limiter)
        else:
            await delivery.deliver_clip(
                message, self.bot, self.config, self.services, url)
        return time.monotonic() - started, message


def stub(monkey_probe, monkey_download):
    extract.probe = monkey_probe
    fetch.download_items = monkey_download
    delivery.probe = monkey_probe


async def drive(rig, urls, concurrency, **send_kwargs):
    latencies, messages = [], []
    gate = asyncio.Semaphore(concurrency)

    async def one(url):
        async with gate:
            elapsed, message = await rig.send(url, **send_kwargs)
            latencies.append(elapsed)
            messages.append(message)

    started = time.monotonic()
    await asyncio.gather(*(one(url) for url in urls))
    return latencies, time.monotonic() - started, messages


async def scenario_cold(workdir, count, concurrency, probe_s, download_s, size_mb):
    stub(make_probe(probe_s), make_download(download_s, int(size_mb * 1024 * 1024)))
    rig = Rig(workdir, max_concurrent_downloads=concurrency)
    urls = [f"https://instagram.com/p/LOAD{i}/" for i in range(count)]
    latencies, seconds, messages = await drive(rig, urls, concurrency * 2)
    sent = sum(m.sends for m in messages)
    report(f"cold c={concurrency}", latencies, seconds, f"sent={sent} peak={peak_rss_mb():.0f}MB")
    return latencies


async def scenario_cache(workdir, count):
    stub(make_probe(2.5), make_download(0.2, 3 * 1024 * 1024))
    rig = Rig(workdir)
    url = "https://instagram.com/p/CACHED/"
    await rig.send(url)                                    # warm it
    latencies, seconds, _ = await drive(rig, [url] * count, 8)
    report("cache-hit storm", latencies, seconds, f"peak={peak_rss_mb():.0f}MB")


async def scenario_burst(workdir, count, limit):
    stub(make_probe(0.05), make_download(0.05, 1024))
    rig = Rig(workdir, rate_limit_per_hour=limit)
    urls = [f"https://instagram.com/p/BURST{i}/" for i in range(count)]
    latencies, seconds, messages = await drive(rig, urls, 8)
    limited = sum(1 for m in messages if any("rate limit" in r for r in m.replies))
    report(f"burst of {count}, limit {limit}", latencies, seconds,
           f"allowed={count - limited} refused={limited}")


async def scenario_pacing(workdir, count, interval):
    stub(make_probe(0.05), make_download(0.05, 1024))
    rig = Rig(workdir, chat_send_interval_seconds=interval)
    urls = [f"https://instagram.com/p/PACE{i}/" for i in range(count)]
    latencies, seconds, _ = await drive(rig, urls, count)
    expected = (count - 1) * interval
    report(f"{count} sends, {interval}s chat pace", latencies, seconds,
           f"expected>={expected:.0f}s {'OK' if seconds >= expected else 'TOO FAST'}")


async def scenario_failures(workdir):
    print("  failure injection:")
    for label, error in [
        ("rejected (needs_login)", ClipRejected("needs a login", "needs_login")),
        ("unavailable (network)", ClipUnavailable("network problem", "network")),
        ("unexpected (bug)", RuntimeError("boom")),
    ]:
        stub(make_probe(0.02, error=error), make_download(0.02, 1024))
        rig = Rig(workdir)
        _, message = await rig.send("https://instagram.com/p/FAIL/", through_handler=False)
        leftovers = len(list(Path(tempfile.gettempdir()).glob("socialdl-*")))
        print(f"    {label:<24} replied={bool(message.replies)} "
              f"temp_left={leftovers} reply={message.replies[0][:44] if message.replies else '-'}")


async def scenario_cotenancy(workdir, count, concurrency):
    stop = asyncio.Event()
    baseline: list[float] = []
    sampler = asyncio.create_task(sample_latency(PWA_URL, stop, baseline, 0.25))
    await asyncio.sleep(5)
    stop.set()
    await sampler
    print(f"  pwa baseline               n={len(baseline)} "
          f"p50={percentile(baseline, .5) * 1000:.0f}ms p95={percentile(baseline, .95) * 1000:.0f}ms")

    stop = asyncio.Event()
    under_load: list[float] = []
    sampler = asyncio.create_task(sample_latency(PWA_URL, stop, under_load, 0.25))
    await scenario_cold(workdir, count, concurrency, 2.0, 0.3, 5)
    stop.set()
    await sampler
    print(f"  pwa under load             n={len(under_load)} "
          f"p50={percentile(under_load, .5) * 1000:.0f}ms p95={percentile(under_load, .95) * 1000:.0f}ms")
    return baseline, under_load


async def scenario_transcode(workdir, clips):
    """Real ffmpeg, to prove serialisation holds and the co-hosted app survives."""
    import subprocess
    from src.media import transcode

    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "big.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", "testsrc2=size=1280x720:rate=30", "-f", "lavfi", "-i", "sine",
        "-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "8000k",
        "-c:a", "aac", str(source)], check=True)
    size_mb = source.stat().st_size / 1024 / 1024
    print(f"  source clip: {size_mb:.0f}MB, 30s of 720p")

    transcode.configure(1)
    config = Config(bot_token="x", max_filesize_mb=10, min_free_memory_mb=200)

    stop = asyncio.Event()
    samples: list[float] = []
    sampler = asyncio.create_task(sample_latency(PWA_URL, stop, samples, 0.25))

    def one(index):
        copy = workdir / f"copy{index}.mp4"
        copy.write_bytes(source.read_bytes())
        started = time.monotonic()
        transcode.shrink_to_limit(copy, 30, config)
        return time.monotonic() - started

    started = time.monotonic()
    durations = await asyncio.gather(*(asyncio.to_thread(one, i) for i in range(clips)))
    wall = time.monotonic() - started
    stop.set()
    await sampler

    # The later timings include waiting on the semaphore, so summing them would
    # double-count the queue. Serialisation shows as: total wall clock equals the
    # longest observed time, and that is roughly N times the shortest.
    quickest, slowest = min(durations), max(durations)
    serialised = wall >= quickest * (clips * 0.85) and abs(wall - slowest) < 5
    print(f"  {clips} re-encodes: wall={wall:.0f}s each={[f'{d:.0f}s' for d in durations]}")
    print(f"  encode alone ~{quickest:.0f}s, wall {wall:.0f}s "
          f"-> serialised: {serialised}")
    print(f"  pwa during transcode       n={len(samples)} "
          f"p50={percentile(samples, .5) * 1000:.0f}ms p95={percentile(samples, .95) * 1000:.0f}ms")


async def scenario_soak(workdir, minutes, interval):
    stub(make_probe(0.3), make_download(0.2, 2 * 1024 * 1024))
    rig = Rig(workdir)
    deadline = time.monotonic() + minutes * 60
    start_rss, index, latencies = rss_mb(), 0, []
    print(f"  soak {minutes}min, one request every {interval}s, start rss={start_rss:.0f}MB")
    while time.monotonic() < deadline:
        elapsed, _ = await rig.send(f"https://instagram.com/p/SOAK{index}/")
        latencies.append(elapsed)
        index += 1
        await asyncio.sleep(interval)
    leftovers = len(list(Path(tempfile.gettempdir()).glob("socialdl-*")))
    print(f"  soak done: n={index} rss {start_rss:.0f} -> {rss_mb():.0f}MB "
          f"(drift {rss_mb() - start_rss:+.0f}MB) temp_left={leftovers} "
          f"p95={percentile(latencies, .95):.2f}s")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["all", "cold", "cache", "burst", "pacing",
                                             "failures", "cotenancy", "transcode", "soak"])
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--minutes", type=float, default=5)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="loadtest-") as directory:
        workdir = Path(directory)
        print(f"rss at start: {rss_mb():.0f}MB (peak {peak_rss_mb():.0f}MB)\n")

        if args.scenario in ("all", "cache"):
            await scenario_cache(workdir / "cache", args.count * 5)
        if args.scenario in ("all", "cold"):
            for concurrency in ([args.concurrency] if args.scenario == "cold"
                                else [1, 2, 3, 4, 8]):
                await scenario_cold(workdir / f"c{concurrency}", args.count,
                                    concurrency, 2.0, 0.3, 5)
        if args.scenario in ("all", "burst"):
            await scenario_burst(workdir / "burst", 60, 30)
        if args.scenario in ("all", "pacing"):
            await scenario_pacing(workdir / "pace", 5, 3.0)
        if args.scenario in ("all", "failures"):
            await scenario_failures(workdir / "fail")
        if args.scenario in ("all", "cotenancy"):
            await scenario_cotenancy(workdir / "coten", args.count, 3)
        if args.scenario in ("all", "transcode"):
            await scenario_transcode(workdir / "transcode", 2)
        if args.scenario == "soak":
            await scenario_soak(workdir / "soak", args.minutes, 2.0)

        print(f"\nrss at end: {rss_mb():.0f}MB (peak {peak_rss_mb():.0f}MB)")


if __name__ == "__main__":
    asyncio.run(main())
