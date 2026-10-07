"""Display the frozen-policy pilot's S3 records in TensorBoard.

Two sequential downloads <=16 MiB each; parsed records <=256 MiB estimated.
Run the exporter and server under separate 2 GiB cgroups on cpu-angad.
One exporter owns each event directory. Original S3 records remain authoritative.
The private sources.json contains expiring signed URLs and must not be archived.
"""

import argparse
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from tensorboardX import SummaryWriter


def fetch(url):
    try:
        with urlopen(url, timeout=20) as response:
            data = response.read(16 * 1024**2 + 1)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(f"Source request returned HTTP {error.code}") from None
    except URLError:
        raise RuntimeError("Source connection failed") from None
    if len(data) > 16 * 1024**2:
        raise RuntimeError("Source exceeds the registered download cap")
    return data


def rows(data):
    if data is None:
        return []
    lines = data.splitlines()
    if data and not data.endswith(b"\n"):
        lines = lines[:-1]
    return [json.loads(line) for line in lines if line.strip()]


def export_grpo(sources, logdir):
    """One writer per arm; import actual trainer logs without smoothing."""
    writers = {arm: SummaryWriter(str(logdir / arm), flush_secs=5) for arm in sources["training_logs"]}
    progress = SummaryWriter(str(logdir / "progress"), flush_secs=5)
    seen = {arm: 0 for arm in writers}
    seen_capability = set()
    try:
        while time.time() < sources["stop_at"]:
            for arm, url in sources["training_logs"].items():
                records = rows(fetch(url))
                for record in records[seen[arm]:]:
                    for tag, value in record.items():
                        if isinstance(value, (int, float)) and tag != "step":
                            writers[arm].add_scalar("grpo/" + tag, value, record["step"])
                seen[arm] = len(records)
                writers[arm].flush()
            print(json.dumps(seen), flush=True)
            if sources.get('capability_curve'):
                for point in rows(fetch(sources['capability_curve'])):
                    if point['phase'] in seen_capability:
                        continue
                    split = 'validation' if point['phase'].startswith('validation') else 'heldout'
                    step = point['trajectory_training_generated_tokens']
                    for tag in ('accepted', 'n', 'unparsed', 'at_generation_cap'):
                        progress.add_scalar('capability/' + split + '/' + tag, point[tag], step)
                    progress.add_scalar('capability/' + split + '/accuracy', point['accepted'] / point['n'], step)
                    seen_capability.add(point['phase'])
            data = fetch(sources["run_record"])
            if data is not None:
                try:
                    run = json.loads(data)
                except json.JSONDecodeError:
                    print("RUN_RECORD_PARSE_FAILED: retrying incomplete live snapshot", flush=True)
                else:
                    counts = run.get('counts')
                    if counts is None:
                        counts = {k: run[k] for k in ('rollouts', 'optimizer_steps', 'training_generated_tokens', 'evaluation_generated_tokens', 'training_prompt_tokens', 'evaluation_prompt_tokens', 'scorer_input_tokens') if k in run}
                        step = run['optimizer_steps']
                    else:
                        step = counts['rollout_attempts']
                    for tag, value in counts.items():
                        progress.add_scalar("pilot/" + tag, value, step)
                    progress.add_scalar("pilot/wall_seconds", run["wall_seconds"], step)
                    progress.flush()
                    if run["status"] in ("complete", "failed", "no_informative_training_groups"):
                        if not sources.get('capability_curve') or run['status'] != 'complete' or 'validation_final' in seen_capability:
                            break
            time.sleep(30)
    finally:
        for writer in writers.values():
            writer.close()
        progress.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    sources = json.loads((args.root / "sources.json").read_text())
    logdir = Path(sources.get("logdir", args.root / "events"))
    logdir.mkdir(exist_ok=False)
    if sources.get("type") == "grpo":
        export_grpo(sources, logdir)
        return
    writers, counters = {}, {}
    generated, checked = set(), set()
    while time.time() < sources["stop_at"]:
        responses = rows(fetch(sources["urls"]["responses.jsonl"]))
        verification = rows(fetch(sources["urls"]["verification.jsonl"]))
        by_key = {(r["id"], r["temperature"], r["attempt"]): r for r in responses}
        for record, is_check in [(r, False) for r in responses] + [(r, True) for r in verification]:
            key = (record["id"], record["temperature"], record["attempt"])
            seen = checked if is_check else generated
            if key in seen or (is_check and key not in by_key):
                continue
            seen.add(key)
            for name in ("overall", f"temperature_{record['temperature']}"):
                if name not in writers:
                    writers[name] = SummaryWriter(str(logdir / name), flush_secs=5)
                    counters[name] = dict(attempts=0, checks=0, accepted=0, tokens=0, seconds=0, capped=0)
                writer, count = writers[name], counters[name]
                if is_check:
                    count["checks"] += 1
                    count["accepted"] += int(record["accepted"])
                    step = count["checks"]
                    metrics = {
                        "proof/verified_attempts": step,
                        "proof/accepted_total": count["accepted"],
                        "proof/accepted_fraction": count["accepted"] / step,
                        "proof/accepted_this_attempt": int(record["accepted"]),
                    }
                else:
                    count["attempts"] += 1
                    count["tokens"] += record["generated_tokens"]
                    count["seconds"] += record["generation_wall_seconds"]
                    count["capped"] += int(record["hit_token_cap"])
                    step = count["attempts"]
                    metrics = {
                        "progress/generated_attempts": step,
                        "tokens/generated_total": count["tokens"],
                        "tokens/completion": record["generated_tokens"],
                        "generation/wall_seconds_total": count["seconds"],
                        "generation/tokens_per_second": count["tokens"] / count["seconds"],
                        "generation/token_cap_fraction": count["capped"] / step,
                        "gpu/peak_allocated_bytes": record["cuda_peak_bytes"],
                    }
                for tag, value in metrics.items():
                    writer.add_scalar(tag, value, step)
        for writer in writers.values():
            writer.flush()
        print(json.dumps(dict(generated_attempts=len(generated), verified_attempts=len(checked))), flush=True)
        summary = fetch(sources["urls"]["summary.json"])
        if summary is not None:
            (args.root / "summary.json").write_bytes(summary)
            final = json.loads(summary)
            if len(generated) == final["attempts"] and len(checked) == final["attempts"]:
                break
        time.sleep(30)
    for writer in writers.values():
        writer.close()


if __name__ == "__main__":
    main()
