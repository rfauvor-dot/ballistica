"""PROTOTYPE live shot detector, replayed over a range video (not wired into the app yet).
Strictly causal: each frame is only compared with what came BEFORE it, as a live feed would be.
Per sheet it keeps a memory of confirmed holes, recognizes the sheet again by that hole pattern
(so a camera swing away and back does not re-call old holes), calls a new hole once it persists
for N_CONF frames, and saves the camera frame plus the flattened sheet with the hole ringed.
First tested 2026-11-13 on a 22-minute range video; see MULTI_TENANCY_DESIGN.md section 40.
usage: python tools/live_shot_replay.py VIDEO OUTDIR FPS SCALE PPI [N_CONF] [START_S] [END_S]
e.g.   python tools/live_shot_replay.py range.mp4 out 2 1.0 100 3
"""
import cv2, sys, json, math, time, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballistica import target

VIDEO, OUT = sys.argv[1], sys.argv[2]
FPS = float(sys.argv[3]); SCALE = float(sys.argv[4]); PPI = float(sys.argv[5])
N_CONF = int(sys.argv[6]) if len(sys.argv) > 6 else 3
T0 = float(sys.argv[7]) if len(sys.argv) > 7 else 0
T1 = float(sys.argv[8]) if len(sys.argv) > 8 else 1e9
os.makedirs(OUT, exist_ok=True)

_orig = cv2.aruco.DetectorParameters
def tuned():
    p = _orig()
    p.polygonalApproxAccuracyRate = 0.1; p.adaptiveThreshWinSizeMax = 83; p.adaptiveThreshWinSizeStep = 4
    p.errorCorrectionRate = 1.0; p.perspectiveRemovePixelPerCell = 8; p.perspectiveRemoveIgnoredMarginPerCell = 0.2
    p.minMarkerDistanceRate = 0.02; p.minCornerDistanceRate = 0.02
    return p
det = cv2.aruco.ArucoDetector(target._aruco_dict(), tuned())
O = target._MARKER_ORIGINS
W, H = int(8.5 * PPI), int(11 * PPI)
AIM = (4.25 * PPI, 5.5 * PPI)
mask = np.zeros((H, W), np.uint8)
mask[int(1.55*PPI):int(10.2*PPI), int(0.3*PPI):int(8.2*PPI)] = 255
for (_ox, _oy) in O.values():
    mask[int((_oy-0.35)*PPI):int((_oy+1.35)*PPI), int((_ox-0.35)*PPI):int((_ox+1.35)*PPI)] = 0
K = int(PPI * 101 / 200) | 1           # median-blur kernel, scaled
AREA_LO, AREA_HI = 400 * (PPI/200)**2, 2500 * (PPI/200)**2
MORPH = max(3, int(25 * PPI / 200))

TOL_KNOWN = 0.30      # a dot this close to a confirmed hole is that hole
TOL_CAND = 0.20       # candidate matching between frames
tracks = []           # sheet tracks
shots = []            # emitted shots
timing = dict(decode=[], markers=[], rectify=[], total=[])

def clusters_in(gray):
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None: return []
    votes = []
    for c, i in zip(corners, ids.ravel().tolist()):
        if i not in O: continue
        c = c.reshape(4, 2)
        s = float(np.mean([np.linalg.norm(c[k]-c[(k+1) % 4]) for k in range(4)]))
        ctr = c.mean(0); ox, oy = O[i]
        votes.append(dict(id=i, c=c, s=s, org=(ctr[0]-(ox+0.5)*s, ctr[1]-(oy+0.5)*s)))
    cl_list = []
    for v in sorted(votes, key=lambda v: v['org'][0]):
        for cl in cl_list:
            m = cl[0]
            if abs(v['org'][0]-m['org'][0]) < 1.3*m['s'] and abs(v['org'][1]-m['org'][1]) < 1.3*m['s']:
                cl.append(v); break
        else:
            cl_list.append([v])
    res = []
    for cl in cl_list:
        byid = {}
        for v in cl: byid.setdefault(v['id'], v)
        if len(byid) < 2: continue
        src, dst = [], []
        for i, v in byid.items():
            ox, oy = O[i]
            src.extend(v['c'].tolist()); dst.extend([(ox, oy), (ox+1, oy), (ox+1, oy+1), (ox, oy+1)])
        src = np.array(src, np.float32); dst = np.array(dst, np.float32)
        if len(byid) >= 3:
            Hm, _ = cv2.findHomography(src, dst, cv2.RANSAC, 0.05)
        else:
            A, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
            Hm = None if A is None else np.vstack([A, [0, 0, 1]])
        if Hm is None: continue
        proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), Hm).reshape(-1, 2)
        rms = float(np.sqrt(((proj-dst)**2).sum(1).mean()))
        res.append(dict(Hm=Hm, rms=rms, found=len(byid), org=(float(np.mean([v['org'][0] for v in cl])), float(np.mean([v['org'][1] for v in cl]))),
                        s=float(np.mean([v['s'] for v in cl]))))
    return res

def dots_of(frame, Hm):
    M = np.diag([PPI, PPI, 1.0]) @ Hm
    rect = cv2.warpPerspective(frame, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    g = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
    diff = cv2.subtract(cv2.medianBlur(g, K), g)
    valid = cv2.erode(cv2.warpPerspective(np.full(frame.shape[:2], 255, np.uint8), M, (W, H), flags=cv2.INTER_NEAREST, borderValue=0), np.ones((MORPH, MORPH), np.uint8))
    cover = float((valid[mask > 0] > 0).mean())
    bw = ((diff > 45) & (mask > 0) & (valid > 0)).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(bw)
    dots = [((cent[i][0]-AIM[0])/PPI, (AIM[1]-cent[i][1])/PPI) for i in range(1, n) if AREA_LO < stats[i, cv2.CC_STAT_AREA] < AREA_HI]
    return rect, cover, dots

def pick_track(cl, dots, t):
    """Causal sheet identity. 1) a track whose confirmed holes are mostly visible in this view IS this sheet
    (hole-pattern memory, works wherever the camera points); 2) else follow image position to a track that has
    too few holes to contradict it; 3) else a new sheet."""
    def hits(tr):
        return sum(1 for h in tr['holes'] if any(math.dist((h['x'], h['y']), d) < 0.35 for d in dots))
    best, bs = None, 0.0
    for tr in tracks:
        n = len(tr['holes'])
        if n == 0: continue
        h = hits(tr)
        frac = h / n
        if h >= min(n, 2) and frac >= 0.7 and (frac, h) > (bs, 0):
            best, bs = tr, frac
    if best is None:
        near, bd = None, 1e9
        for tr in tracks:
            d = math.hypot(cl['org'][0]-tr['org'][0], cl['org'][1]-tr['org'][1]) / cl['s']
            if d < 3.0 and d < bd: near, bd = tr, d
        if near is not None and (len(near['holes']) == 0 or hits(near) >= 0.5 * len(near['holes'])):
            best = near
    if best is None:
        best = dict(id=len(tracks)+1, org=cl['org'], holes=[], cands=[], seen_frames=0, last_t=t)
        tracks.append(best)
    best['org'] = cl['org']
    return best

def update_track(tr, dots, t, rect, frame):
    seen_c = set()
    for d in dots:
        known = [h for h in tr['holes'] if math.dist((h['x'], h['y']), d) < TOL_KNOWN]
        if known:
            k = known[0]; k['obs'].append(d); continue
        hit = None
        for ci, c in enumerate(tr['cands']):
            if math.dist((c['x'], c['y']), d) < TOL_CAND: hit = ci; break
        if hit is None:
            tr['cands'].append(dict(x=d[0], y=d[1], first=t, seen=1, miss=0, pts=[d], rect=rect.copy(), frame=frame, gap=tr.get('gap', 0)))
            seen_c.add(len(tr['cands'])-1)
        else:
            c = tr['cands'][hit]; c['seen'] += 1; c['miss'] = 0; c['pts'].append(d); seen_c.add(hit)
            c['x'] = float(np.median([p[0] for p in c['pts']])); c['y'] = float(np.median([p[1] for p in c['pts']]))
    keep = []
    for ci, c in enumerate(tr['cands']):
        if ci not in seen_c: c['miss'] += 1
        if c['seen'] >= N_CONF:
            tr['holes'].append(dict(x=c['x'], y=c['y'], first=c['first'], obs=list(c['pts'])))
            shot = dict(sheet_track=tr['id'], t_first=c['first'], t_call=t, x=c['x'], y=c['y'], off_camera_s=round(c['gap'], 1) if c['gap'] > 20 else 0)
            n = len(shots) + 1
            # still frames pulled automatically: full camera frame + flattened sheet with the new hole ringed
            r = c['rect'].copy()
            px, py = int(AIM[0] + c['x']*PPI), int(AIM[1] - c['y']*PPI)
            cv2.circle(r, (px, py), int(0.45*PPI), (0, 0, 255), max(2, int(PPI/50)))
            cv2.imwrite(os.path.join(OUT, 'shot%03d_sheet.jpg' % n), cv2.resize(r, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 85])
            cv2.imwrite(os.path.join(OUT, 'shot%03d_frame.jpg' % n), c['frame'], [cv2.IMWRITE_JPEG_QUALITY, 85])
            shot['n'] = n
            shots.append(shot)
            print('SHOT %d  track %d  first %.1fs  called %.1fs  x=%+.2f y=%+.2f' % (n, tr['id'], c['first'], t, c['x'], c['y']), flush=True)
        elif c['miss'] < 2:
            keep.append(c)
    tr['cands'] = keep

cap = cv2.VideoCapture(VIDEO)
vfps = cap.get(cv2.CAP_PROP_FPS)
step = max(1, int(round(vfps / FPS)))
if T0 > 0: cap.set(cv2.CAP_PROP_POS_MSEC, T0*1000)
idx = int(T0 * vfps)
frames_used = frames_gated = 0
wall0 = time.time()
while True:
    t_dec = time.time()
    ok = cap.grab()
    if not ok: break
    if idx % step:
        idx += 1; continue
    ok, fr = cap.retrieve()
    timing['decode'].append(time.time()-t_dec)
    t = idx / vfps
    idx += 1
    if t > T1: break
    t_a = time.time()
    if SCALE != 1.0: fr = cv2.resize(fr, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    cls = clusters_in(gray)
    t_b = time.time()
    for cl in cls:
        good = (cl['found'] >= 3 and cl['rms'] < 0.12) or (cl['found'] == 2 and cl['rms'] < 0.05)
        if not good:
            frames_gated += 1; continue
        rect, cover, dots = dots_of(fr, cl['Hm'])
        if cover < 0.9:
            frames_gated += 1; continue
        tr = pick_track(cl, dots, t)
        gap = t - tr['last_t']; tr['gap'] = gap; tr['last_t'] = t
        tr['seen_frames'] += 1
        update_track(tr, dots, t, rect, fr)
        frames_used += 1
    t_c = time.time()
    timing['markers'].append(t_b-t_a); timing['rectify'].append(t_c-t_b); timing['total'].append(t_c-t_a)
    if int(t) % 120 == 0 and len(timing['total']) % int(max(1, FPS)*2) == 0:
        print('t=%ds  shots=%d  tracks=%d' % (int(t), len(shots), len(tracks)), flush=True)

def stat(v): return dict(mean_ms=round(1000*float(np.mean(v)), 1), p95_ms=round(1000*float(np.percentile(v, 95)), 1)) if v else {}
summary = dict(fps=FPS, scale=SCALE, ppi=PPI, n_conf=N_CONF, frames_processed=len(timing['total']), sheet_frames_used=frames_used, sheet_frames_gated=frames_gated,
               wall_s=round(time.time()-wall0, 1), decode=stat(timing['decode']), markers=stat(timing['markers']), rectify=stat(timing['rectify']), total=stat(timing['total']),
               shots=[{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()} for s in shots],
               tracks=[dict(id=tr['id'], holes=len(tr['holes']), frames=tr['seen_frames']) for tr in tracks])
json.dump(summary, open(os.path.join(OUT, 'summary.json'), 'w'), indent=1)
print('DONE', json.dumps({k: v for k, v in summary.items() if k != 'shots'}), flush=True)
