#!/usr/bin/env python3
"""Compare a captured flat-floor movement reference with Horizon's existing physics probe.

Usage: python tools/check_ground_reference.py WORLD_PROBE REFERENCE_JSON
The input contains walk_ordinary, sprint_ordinary and jump_ordinary tick traces
(position, velocity) plus diagonal_inputs (yaw, vector), jump_strength and gravity.
Only the flat floor, input and the small-velocity cutoff are supplied by this
driver. Horizon performs acceleration, input rotation, friction and gravity.
"""
import argparse
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile


def bits(value, fmt):
    return struct.pack('>' + fmt, value).hex()


def f32(value):
    return struct.unpack('>f', struct.pack('>f', value))[0]


def vector(values):
    return [bits(v, 'd') for v in values]


def sample(mode, velocity, grounded, speed, yaw, axes, gravity):
    return dict(mode=mode, velocity=vector(velocity), fall=bits(0, 'd'), fail=-1,
                ground=[grounded], climb=[False],
                friction=bits(.6, 'f'), friction_modifier=bits(1, 'd'),
                speed=bits(speed, 'f'), flying_speed=bits(.02, 'f'), yaw=bits(yaw, 'f'),
                yaw_mutate=False, input=vector(axes), move_mutate=False,
                after_horizontal=False, after_jumping=False, after_snow=False,
                levitation=None, client=False, gravity=bits(gravity, 'd'), discard=False,
                drag_modifier=bits(1, 'd'), omni=False)


def decode(values):
    return [struct.unpack('>d', bytes.fromhex(v))[0] for v in values]


def run(probe, reference):
    fixtures = []
    expected = []
    for row in reference['diagonal_inputs']:
        fixtures.append(sample('input', [0]*3, True, .1, row['yaw'],
                               [-f32(.98), 0, f32(.98)], reference['gravity']))
        expected.append(('diagonal', row['yaw'], None, None, row['vector']))
    for name in ('walk_ordinary', 'sprint_ordinary', 'jump_ordinary'):
        pos, vel = [0.]*3, [0.]*3
        rows = reference[name]
        if not rows or any(row['tick'] != i for i,row in enumerate(rows,1)):
            raise ValueError(f'Invalid tick sequence: {name}')
        for i, row in enumerate(rows):
            # Drive each step from the capture so a divergence points to its first cause.
            vel = [v if abs(v) >= .003 else 0. for v in vel]
            jumping = name == 'jump_ordinary'
            if jumping and i == 0:
                vel[1] = f32(reference['jump_strength'])
            f = sample('air', vel, pos[1] == 0., .13 if name=='sprint_ordinary' else .1,
                       0, [0,0,0 if jumping else f32(.98)], reference['gravity'])
            # Vanilla clips downward motion at the floor before applying gravity.
            if pos[1] + vel[1] < 0:
                f.update(move_mutate=True, after_velocity=vector([vel[0], 0, vel[2]]))
                # The horizontal components used after landing include this tick's input.
                # The callback treats X/Z as the incoming move, captured by the raw move line.
            fixtures.append(f)
            expected.append((name, i+1, pos, row['position'], row['velocity']))
            pos, vel = row['position'], row['velocity']
    if not reference['diagonal_inputs']:
        raise ValueError('No diagonal inputs')

    def execute(directory):
        (directory/'inputs.txt').write_text(''.join(json.dumps(f)+'\n' for f in fixtures),encoding='utf8')
        subprocess.run([probe,'living_air_fixture',directory],check=True)
        blocks = []
        lines = []
        for line in (directory/'rust.txt').read_text().splitlines():
            lines.append(line.split())
            if line.startswith('queries '):
                blocks.append(lines)
                lines = []
        if len(blocks) != len(fixtures) or lines:
            raise ValueError('Incomplete Horizon probe output')
        return blocks

    def field(lines, key):
        return next(x[1:] for x in lines if x[0]==key)

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        blocks = execute(directory)
        for f, lines in zip(fixtures,blocks):
            if f['move_mutate']:
                movement = field(lines,'move')
                f['after_velocity'] = [movement[0], bits(0,'d'), movement[2]]
        blocks = execute(directory)
    errors = {}
    for (kind, tick, p0, p1, v1), lines in zip(expected,blocks):
        if field(lines,'result') != ['ok']:
            raise ValueError(f'Horizon probe failed: {kind} {tick}')
        if kind=='diagonal':
            predicted = decode(field(lines,'vector'))
            measured = v1
        else:
            move = decode(field(lines,'move'))
            p = [pos+delta for pos,delta in zip(p0,move)]
            p[1] = max(p[1],0.)
            predicted = p + decode(field(lines,'final')[:3])
            measured = p1 + v1
        if len(measured)!=len(predicted) or not all(math.isfinite(x) for x in measured+predicted):
            raise ValueError('Invalid reference or output component')
        delta = max(abs(a-b) for a,b in zip(measured,predicted))
        stats = errors.setdefault(kind,dict(samples=0,max_absolute_error=0,first_mismatch=None))
        stats['samples'] += 1
        stats['max_absolute_error'] = max(delta,stats['max_absolute_error'])
        if delta > 1e-7 and stats['first_mismatch'] is None:
            stats['first_mismatch'] = tick
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('world_probe',type=Path)
    parser.add_argument('reference_json',type=Path)
    args = parser.parse_args()
    report = run(args.world_probe.resolve(),json.loads(args.reference_json.read_text(encoding='utf8')))
    print(json.dumps(report,indent=2))
    raise SystemExit(any(s['first_mismatch'] is not None for s in report.values()))
