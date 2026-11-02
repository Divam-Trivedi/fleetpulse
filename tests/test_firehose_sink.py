import asyncio
import json

from fleetpulse.sinks import FirehoseSink


class FakeFirehose:
    def __init__(self, fail_first=0):
        self.calls, self.received, self._fail = [], [], fail_first

    def put_record_batch(self, DeliveryStreamName, Records):
        self.calls.append(len(Records))
        out = []
        for r in Records:
            if self._fail > 0:
                self._fail -= 1
                out.append({"ErrorCode": "ServiceUnavailableException"})
            else:
                self.received.append(r["Data"])
                out.append({"RecordId": "x"})
        return {"FailedPutCount": sum("ErrorCode" in o for o in out), "RequestResponses": out}


def run_sink(n, fake, **kw):
    async def go():
        s = FirehoseSink("s", client=fake, **kw)
        for i in range(n):
            await s.write({"i": i})
        await s.close()
        return s
    return asyncio.run(go())


def test_batches_and_newline_delimited():
    fake = FakeFirehose()
    s = run_sink(1200, fake, batch_size=500, flush_interval_s=999)
    assert sorted(fake.calls, reverse=True) == [500, 500, 200]
    assert s.stats["sent"] == 1200
    assert all(d.endswith(b"\n") for d in fake.received)
    assert json.loads(fake.received[0]) == {"i": 0}


def test_retries_failed_records_only():
    fake = FakeFirehose(fail_first=3)
    s = run_sink(10, fake, batch_size=10, flush_interval_s=999)
    assert s.stats["sent"] == 10 and s.stats["dropped"] == 0
    assert len(fake.received) == 10
    assert fake.calls == [10, 3]


def test_drops_after_max_retries():
    fake = FakeFirehose(fail_first=10_000)
    s = run_sink(5, fake, batch_size=5, flush_interval_s=999, max_retries=1)
    assert s.stats["dropped"] == 5


def test_write_never_blocks_on_slow_network():
    import time

    class Slow(FakeFirehose):
        def put_record_batch(self, **kw):
            time.sleep(0.3)
            return super().put_record_batch(**kw)

    async def go():
        s = FirehoseSink("s", client=Slow(), batch_size=50, flush_interval_s=0.05)
        t0 = time.monotonic()
        for i in range(2000):
            await s.write({"i": i})
            await asyncio.sleep(0)          # let the event loop run, as robots would
        elapsed = time.monotonic() - t0
        await s.close()
        return s, elapsed

    s, elapsed = asyncio.run(go())
    assert elapsed < 0.25                   # writes were not stalled by 0.3s calls
    assert s.stats["sent"] == 2000 and s.stats["dropped"] == 0


def test_overflow_is_dropped_not_blocking():
    s = run_sink(100, FakeFirehose(), batch_size=500, flush_interval_s=999, max_buffer=10)
    assert s.stats["dropped"] == 90 and s.stats["sent"] == 10
