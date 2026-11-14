import json, math, sys, csv
d = sys.argv[1]
GT_CSV = sys.argv[2]  # hand-measured shots CSV: sheet,shot_in_stretch,video_time,video_seconds,x_in_right_of_crosshair,y_in_above_crosshair
S = json.load(open(d + '/summary.json'))
gt = []
for r in csv.DictReader(open(GT_CSV)):
    if r['sheet'] and r['sheet'] != 'note' and r['video_seconds']:
        gt.append(dict(sheet=r['sheet'], t=float(r['video_seconds']), x=float(r['x_in_right_of_crosshair']), y=float(r['y_in_above_crosshair'])))
shots = S['shots']
used = set(); rows = []
for g in gt:
    best = None
    for i, s in enumerate(shots):
        if i in used: continue
        dt = s['t_first'] - g['t']
        dp = math.dist((s['x'], s['y']), (g['x'], g['y']))
        if -6 <= dt <= 10 and dp < 0.4 and (best is None or dp < best[1]): best = (i, dp, dt)
    if best:
        used.add(best[0]); rows.append((g, shots[best[0]], best[1], best[2], best[0]+1))
    else:
        rows.append((g, None, None, None, None))
print('GT shots:', len(gt), ' detected & matched:', sum(1 for r in rows if r[1]), ' misses:', sum(1 for r in rows if not r[1]))
for g, s, dp, dt, n in rows:
    tag = 'sheet %-8s %5.0fs (%+.2f,%+.2f)' % (g['sheet'], g['t'], g['x'], g['y'])
    print(tag, '->', ('MATCH shot#%d  err %.2f in, first-seen %+.1fs, called +%.1fs' % (n, dp, dt, s['t_call']-s['t_first'])) if s else 'MISSED')
extra = [(i+1, s) for i, s in enumerate(shots) if i not in used]
print('unmatched detections (need eyeball check):', len(extra))
for n, s in extra:
    print('  shot#%d track %d first %.0fs (%+.2f,%+.2f)' % (n, s['sheet_track'], s['t_first'], s['x'], s['y']))
print({k: S[k] for k in ('frames_processed', 'sheet_frames_used', 'sheet_frames_gated', 'wall_s', 'decode', 'markers', 'rectify', 'total', 'tracks')})
