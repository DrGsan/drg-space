#!/usr/bin/env python3
"""Bounded read-only Linux/cgroup-v2 sampler. No app imports, payloads or secrets.

Run on the Docker host; JSONL goes to stdout. Docker % uses one CPU as 100%.
Pass --samples and --interval for a bounded observation of existing activity.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time


def counters(path):
    try:
        return {k: int(v) for k, v in (line.split()[:2] for line in Path(path).read_text().splitlines())}
    except (OSError, ValueError):
        return {}


def host():
    cpu = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    memory = counters('/proc/meminfo')
    vm = counters('/proc/vmstat')
    pressure = {}
    for resource in ['cpu', 'memory', 'io']:
        try:
            pressure[resource] = {line.split()[0]: dict(item.split('=') for item in line.split()[1:])
                                  for line in Path('/proc/pressure/' + resource).read_text().splitlines()}
        except OSError:
            pass
    return {'cpu_ticks': cpu, 'memory_kib': {k: memory.get(k) for k in ['MemTotal:', 'MemAvailable:', 'SwapTotal:', 'SwapFree:']},
            'swap_in_pages': vm.get('pswpin'), 'swap_out_pages': vm.get('pswpout'), 'pressure': pressure}


def processes(group):
    result = []
    for raw in (group / 'cgroup.procs').read_text().split():
        try:
            text = Path('/proc/' + raw + '/stat').read_text()
            end = text.rindex(')')
            fields = text[end + 2:].split()
            result.append({'pid': int(raw), 'name': text[text.index('(') + 1:end],
                           'start_ticks': int(fields[19]), 'cpu_ticks': int(fields[11]) + int(fields[12]),
                           'major_faults': int(fields[9])})
        except (OSError, ValueError, IndexError):
            pass
    return result


def container(name, group):
    stat = counters(group / 'memory.stat')
    current = int((group / 'memory.current').read_text())
    return {'name': name, 'cpu': counters(group / 'cpu.stat'),
            'memory_bytes': current, 'working_set_bytes': max(0, current - stat.get('inactive_file', 0)),
            'swap_bytes': int((group / 'memory.swap.current').read_text()),
            'pids': int((group / 'pids.current').read_text()), 'processes': processes(group)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=60)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('containers', nargs='*', default=['drg-space-app', 'drg-media-api', 'drg-media-worker'])
    args = parser.parse_args()
    if not 2 <= args.samples <= 1800 or not .5 <= args.interval <= 60:
        parser.error('Use 2..1800 samples and an interval of .5..60 seconds.')
    locations = []
    for name in args.containers:
        pid = int(subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Pid}}', name], text=True).strip())
        cgroup = next(line.split(':', 2)[2] for line in Path('/proc/' + str(pid) + '/cgroup').read_text().splitlines() if line.startswith('0::'))
        locations.append((name, Path('/sys/fs/cgroup') / cgroup.lstrip('/')))
    print(json.dumps({'type': 'metadata', 'cpus': os.cpu_count(), 'clock_ticks': os.sysconf('SC_CLK_TCK'),
                      'page_bytes': os.sysconf('SC_PAGE_SIZE'), 'interval_seconds': args.interval,
                      'samples': args.samples, 'cpu_convention': '100% = one CPU; divide by cpus for host capacity'}), flush=True)
    started = time.monotonic()
    for i in range(args.samples):
        if i:
            time.sleep(max(0, started + i * args.interval - time.monotonic()))
        try:
            row = {'type': 'sample', 'utc': datetime.now(timezone.utc).isoformat(), 'elapsed': time.monotonic() - started,
                   'host': host(), 'containers': [container(name, group) for name, group in locations]}
        except OSError as error:
            raise SystemExit('Container restarted or cgroup unavailable; start a new bounded observation: ' + str(error))
        print(json.dumps(row, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
