/*
 * Client-side port of the ballistics engine (ballistica/trajectory.py,
 * drag_tables.py, atmosphere.py, units.py, reporting.py) for offline use.
 *
 * This is a deliberate line-for-line port, not a reimplementation --
 * every formula, constant, and code path here must match the Python
 * engine exactly, since the whole point is that an offline solution
 * shows the same number a live one would for the same inputs. See
 * scripts/check_engine_parity.js for the test that verifies this
 * against real output from the Python engine.
 *
 * Deliberately NOT ported: zero.py (chronograph calibration/minimum-
 * spread zero solving) and angle.py (incline-angle back-solving) --
 * both are setup-time tools used at home with a connection, out of
 * scope for "offline at the range" (see MULTI_TENANCY_DESIGN.md §19).
 *
 * Works both as a browser <script> (attaches to window.BallisticaEngine)
 * and as a Node CommonJS module (for the parity test).
 */
(function (root) {
  "use strict";

  // ---------------------------------------------------------- atmosphere.py
  const GAMMA_AIR = 1.4;
  const R_DRY = 287.058;
  const R_VAPOR = 461.495;
  const EPSILON = R_DRY / R_VAPOR;

  const SLUG_PER_FT3_TO_KG_PER_M3 = 515.379;
  const MPS_TO_FPS = 3.280839895;
  const INHG_TO_PA = 3386.389;

  const STANDARD_TEMP_F = 59.0;
  const STANDARD_PRESSURE_INHG = 29.92;
  const STANDARD_HUMIDITY_PCT = 0.0;
  const STANDARD_ALTITUDE_FT = 0.0;

  function fToC(tempF) {
    return ((tempF - 32.0) * 5.0) / 9.0;
  }

  function saturationVaporPressurePa(tempC) {
    const hpa = 6.1121 * Math.exp((18.678 - tempC / 234.5) * (tempC / (257.14 + tempC)));
    return hpa * 100.0;
  }

  function pressureAtAltitudeInhg(altitudeFt, seaLevelInhg) {
    if (seaLevelInhg === undefined) seaLevelInhg = 29.92;
    return seaLevelInhg * Math.pow(1.0 - 6.8756e-6 * altitudeFt, 5.2559);
  }

  class AtmosphereConditions {
    constructor(tempF, pressureInhg, humidityPct, altitudeFt) {
      this.tempF = tempF;
      this.pressureInhg = pressureInhg;
      this.humidityPct = humidityPct;
      this.altitudeFt = altitudeFt === undefined ? 0.0 : altitudeFt;
    }

    _virtualTempK() {
      const tempC = fToC(this.tempF);
      const tempK = tempC + 273.15;
      const totalPressurePa = this.pressureInhg * INHG_TO_PA;
      let vaporPressurePa = (this.humidityPct / 100.0) * saturationVaporPressurePa(tempC);
      vaporPressurePa = Math.min(vaporPressurePa, totalPressurePa);
      return tempK / (1.0 - (vaporPressurePa / totalPressurePa) * (1.0 - EPSILON));
    }

    airDensitySlugFt3() {
      const virtualTempK = this._virtualTempK();
      const totalPressurePa = this.pressureInhg * INHG_TO_PA;
      const rhoKgM3 = totalPressurePa / (R_DRY * virtualTempK);
      return rhoKgM3 / SLUG_PER_FT3_TO_KG_PER_M3;
    }

    speedOfSoundFps() {
      const virtualTempK = this._virtualTempK();
      const cMps = Math.sqrt(GAMMA_AIR * R_DRY * virtualTempK);
      return cMps * MPS_TO_FPS;
    }

    densityRatio() {
      return this.airDensitySlugFt3() / STANDARD_ATMOSPHERE.airDensitySlugFt3();
    }
  }

  const STANDARD_ATMOSPHERE = new AtmosphereConditions(
    STANDARD_TEMP_F, STANDARD_PRESSURE_INHG, STANDARD_HUMIDITY_PCT, STANDARD_ALTITUDE_FT
  );

  // ---------------------------------------------------------- drag_tables.py
  const G1 = [
    [0.00, 0.2629], [0.05, 0.2558], [0.10, 0.2487], [0.15, 0.2413],
    [0.20, 0.2344], [0.25, 0.2278], [0.30, 0.2214], [0.35, 0.2155],
    [0.40, 0.2104], [0.45, 0.2061], [0.50, 0.2032], [0.55, 0.2020],
    [0.60, 0.2034], [0.70, 0.2165], [0.725, 0.2230], [0.75, 0.2313],
    [0.775, 0.2417], [0.80, 0.2546], [0.825, 0.2706], [0.85, 0.2901],
    [0.875, 0.3136], [0.90, 0.3415], [0.925, 0.3734], [0.95, 0.4084],
    [0.975, 0.4448], [1.0, 0.4805], [1.025, 0.5136], [1.05, 0.5427],
    [1.075, 0.5677], [1.10, 0.5883], [1.125, 0.6053], [1.15, 0.6191],
    [1.20, 0.6393], [1.25, 0.6518], [1.30, 0.6589], [1.35, 0.6621],
    [1.40, 0.6625], [1.45, 0.6607], [1.50, 0.6573], [1.55, 0.6528],
    [1.60, 0.6474], [1.65, 0.6413], [1.70, 0.6347], [1.75, 0.6280],
    [1.80, 0.6210], [1.85, 0.6141], [1.90, 0.6072], [1.95, 0.6003],
    [2.00, 0.5934], [2.05, 0.5867], [2.10, 0.5804], [2.15, 0.5743],
    [2.20, 0.5685], [2.25, 0.5630], [2.30, 0.5577], [2.35, 0.5527],
    [2.40, 0.5481], [2.45, 0.5438], [2.50, 0.5397], [2.60, 0.5325],
    [2.70, 0.5264], [2.80, 0.5211], [2.90, 0.5168], [3.00, 0.5133],
    [3.10, 0.5105], [3.20, 0.5084], [3.30, 0.5067], [3.40, 0.5054],
    [3.50, 0.5040], [3.60, 0.5030], [3.70, 0.5022], [3.80, 0.5016],
    [3.90, 0.5010], [4.00, 0.5006], [4.20, 0.4998], [4.40, 0.4995],
    [4.60, 0.4992], [4.80, 0.4990], [5.00, 0.4988],
  ];

  const G7 = [
    [0.00, 0.1198], [0.05, 0.1197], [0.10, 0.1196], [0.15, 0.1194],
    [0.20, 0.1193], [0.25, 0.1194], [0.30, 0.1194], [0.35, 0.1194],
    [0.40, 0.1193], [0.45, 0.1193], [0.50, 0.1194], [0.55, 0.1193],
    [0.60, 0.1194], [0.65, 0.1197], [0.70, 0.1202], [0.725, 0.1207],
    [0.75, 0.1215], [0.775, 0.1226], [0.80, 0.1242], [0.825, 0.1266],
    [0.85, 0.1306], [0.875, 0.1368], [0.90, 0.1464], [0.925, 0.1660],
    [0.95, 0.2054], [0.975, 0.2993], [1.0, 0.3803], [1.025, 0.4015],
    [1.05, 0.4043], [1.075, 0.4034], [1.10, 0.4014], [1.125, 0.3987],
    [1.15, 0.3955], [1.20, 0.3884], [1.25, 0.3810], [1.30, 0.3732],
    [1.35, 0.3657], [1.40, 0.3580], [1.50, 0.3440], [1.55, 0.3376],
    [1.60, 0.3315], [1.65, 0.3260], [1.70, 0.3209], [1.75, 0.3160],
    [1.80, 0.3117], [1.85, 0.3078], [1.90, 0.3042], [1.95, 0.3010],
    [2.00, 0.2980], [2.05, 0.2951], [2.10, 0.2922], [2.15, 0.2892],
    [2.20, 0.2864], [2.25, 0.2835], [2.30, 0.2807], [2.35, 0.2779],
    [2.40, 0.2752], [2.45, 0.2725], [2.50, 0.2697], [2.55, 0.2670],
    [2.60, 0.2643], [2.65, 0.2615], [2.70, 0.2588], [2.75, 0.2561],
    [2.80, 0.2533], [2.85, 0.2506], [2.90, 0.2479], [2.95, 0.2451],
    [3.00, 0.2424], [3.10, 0.2368], [3.20, 0.2313], [3.30, 0.2258],
    [3.40, 0.2205], [3.50, 0.2154], [3.60, 0.2106], [3.70, 0.2060],
    [3.80, 0.2017], [3.90, 0.1975], [4.00, 0.1935], [4.20, 0.1861],
    [4.40, 0.1793], [4.60, 0.1730], [4.80, 0.1672], [5.00, 0.1618],
  ];

  const TABLES = { G1: G1, G7: G7 };
  if (G1.length !== 79) throw new Error("G1 table has " + G1.length + " rows, expected 79");
  if (G7.length !== 84) throw new Error("G7 table has " + G7.length + " rows, expected 84");

  function dragCoefficient(dragModel, mach) {
    const table = TABLES[dragModel];
    if (mach <= table[0][0]) return table[0][1];
    const last = table[table.length - 1];
    if (mach >= last[0]) return last[1];

    // bisect_left equivalent: first index whose Mach is >= mach
    let i = 0;
    for (; i < table.length; i++) {
      if (table[i][0] >= mach) break;
    }
    if (table[i][0] === mach) return table[i][1];

    const [m0, cd0] = table[i - 1];
    const [m1, cd1] = table[i];
    const frac = (mach - m0) / (m1 - m0);
    return cd0 + frac * (cd1 - cd0);
  }

  // ---------------------------------------------------------- trajectory.py
  const GRAVITY_FPS2 = 32.17405;
  const STANDARD_DENSITY_LBFT3 = 0.076474;
  const DRAG_K = (STANDARD_DENSITY_LBFT3 * Math.PI) / (4 * 2 * 144);

  const FT_PER_YARD = 3.0;
  const IN_PER_FT = 12.0;
  const MPH_TO_FPS = 5280.0 / 3600.0;

  class WindCondition {
    constructor(speedMph, clockDeg) {
      this.speedMph = speedMph === undefined ? 0.0 : speedMph;
      this.clockDeg = clockDeg === undefined ? 0.0 : clockDeg;
    }

    vectorFps() {
      const speedFps = this.speedMph * MPH_TO_FPS;
      const theta = (this.clockDeg * Math.PI) / 180.0;
      const windX = -speedFps * Math.cos(theta);
      const windZ = -speedFps * Math.sin(theta);
      return [windX, windZ];
    }
  }

  class TrajectoryPoint {
    constructor(timeS, rangeFt, dropFt, windageFt, velocityFps, mach) {
      this.timeS = timeS;
      this.rangeFt = rangeFt;
      this.dropFt = dropFt;
      this.windageFt = windageFt;
      this.velocityFps = velocityFps;
      this.mach = mach;
    }
    get rangeYd() { return this.rangeFt / FT_PER_YARD; }
    get dropIn() { return -this.dropFt * IN_PER_FT; }
    get windageIn() { return this.windageFt * IN_PER_FT; }
  }

  function acceleration(vx, vy, vz, windX, windZ, bc, dragModel, densityRatio, speedOfSoundFps) {
    const relVx = vx - windX;
    const relVy = vy;
    const relVz = vz - windZ;
    const relSpeed = Math.sqrt(relVx * relVx + relVy * relVy + relVz * relVz);
    if (relSpeed < 1e-9) return [0.0, -GRAVITY_FPS2, 0.0];

    const mach = relSpeed / speedOfSoundFps;
    const cd = dragCoefficient(dragModel, mach);
    const sdf = (cd * DRAG_K) / bc;
    const dragMag = densityRatio * relSpeed * relSpeed * sdf;

    const ax = (-dragMag * relVx) / relSpeed;
    const ay = (-dragMag * relVy) / relSpeed - GRAVITY_FPS2;
    const az = (-dragMag * relVz) / relSpeed;
    return [ax, ay, az];
  }

  function rk4Step(state, dt, deriv) {
    function f(s) {
      const [x, y, z, vx, vy, vz] = s;
      const [ax, ay, az] = deriv(vx, vy, vz);
      return [vx, vy, vz, ax, ay, az];
    }
    const k1 = f(state);
    const k2 = f(state.map((v, i) => v + (dt / 2) * k1[i]));
    const k3 = f(state.map((v, i) => v + (dt / 2) * k2[i]));
    const k4 = f(state.map((v, i) => v + dt * k3[i]));
    return state.map((v, i) => v + (dt / 6) * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]));
  }

  function interpPointAtRange(points, targetXFt) {
    if (targetXFt <= points[0].rangeFt) return points[0];
    for (let i = 1; i < points.length; i++) {
      if (points[i].rangeFt >= targetXFt) {
        const p0 = points[i - 1], p1 = points[i];
        const span = p1.rangeFt - p0.rangeFt;
        const frac = span <= 0 ? 0.0 : (targetXFt - p0.rangeFt) / span;
        return new TrajectoryPoint(
          p0.timeS + frac * (p1.timeS - p0.timeS),
          targetXFt,
          p0.dropFt + frac * (p1.dropFt - p0.dropFt),
          p0.windageFt + frac * (p1.windageFt - p0.windageFt),
          p0.velocityFps + frac * (p1.velocityFps - p0.velocityFps),
          p0.mach + frac * (p1.mach - p0.mach)
        );
      }
    }
    return points[points.length - 1];
  }

  class TrajectorySolver {
    constructor(opts) {
      this.muzzleVelocityFps = opts.muzzleVelocityFps;
      this.bc = opts.bc;
      this.dragModel = opts.dragModel;
      this.scopeHeightIn = opts.scopeHeightIn;
      this.atmosphere = opts.atmosphere || STANDARD_ATMOSPHERE;
      this.wind = opts.wind || new WindCondition();
      this.dt = opts.dt === undefined ? 0.0005 : opts.dt;

      if (this.dragModel !== "G1" && this.dragModel !== "G7") {
        throw new Error("drag_model must be 'G1' or 'G7'");
      }
      if (this.bc <= 0) throw new Error("bc must be positive");
    }

    _run(launchAngleRad, maxRangeFt) {
      const densityRatio = this.atmosphere.densityRatio();
      const speedOfSound = this.atmosphere.speedOfSoundFps();
      const [windX, windZ] = this.wind.vectorFps();
      const scopeHeightFt = this.scopeHeightIn / IN_PER_FT;

      const vx0 = this.muzzleVelocityFps * Math.cos(launchAngleRad);
      const vy0 = this.muzzleVelocityFps * Math.sin(launchAngleRad);
      let state = [0.0, -scopeHeightFt, 0.0, vx0, vy0, 0.0];

      const deriv = (vx, vy, vz) =>
        acceleration(vx, vy, vz, windX, windZ, this.bc, this.dragModel, densityRatio, speedOfSound);

      const points = [
        new TrajectoryPoint(0.0, 0.0, state[1], state[2], this.muzzleVelocityFps, this.muzzleVelocityFps / speedOfSound),
      ];

      let t = 0.0;
      const maxTimeS = 15.0;
      while (state[0] < maxRangeFt && t < maxTimeS) {
        state = rk4Step(state, this.dt, deriv);
        t += this.dt;
        if (state[3] <= 0) break;
        const vel = Math.sqrt(state[3] ** 2 + state[4] ** 2 + state[5] ** 2);
        points.push(new TrajectoryPoint(t, state[0], state[1], state[2], vel, vel / speedOfSound));
      }
      return points;
    }

    solveZeroAngle(zeroDistanceYd) {
      const zeroFt = zeroDistanceYd * FT_PER_YARD;
      const heightAtZero = (angle) => {
        const pts = this._run(angle, zeroFt + 5.0);
        return interpPointAtRange(pts, zeroFt).dropFt;
      };

      let lo = (-3.0 * Math.PI) / 180.0;
      let hi = (3.0 * Math.PI) / 180.0;
      let fLo = heightAtZero(lo);
      let fHi = heightAtZero(hi);
      while (fLo > 0) {
        lo -= (1.0 * Math.PI) / 180.0;
        fLo = heightAtZero(lo);
      }
      while (fHi < 0) {
        hi += (1.0 * Math.PI) / 180.0;
        fHi = heightAtZero(hi);
      }

      let mid = (lo + hi) / 2;
      for (let i = 0; i < 40; i++) {
        mid = (lo + hi) / 2;
        const fMid = heightAtZero(mid);
        if (Math.abs(fMid) < 1e-5) return mid;
        if (fMid < 0) lo = mid;
        else hi = mid;
      }
      return (lo + hi) / 2;
    }

    trajectory(zeroDistanceYd, maxRangeYd, launchAngleRad) {
      const angle = launchAngleRad === undefined || launchAngleRad === null
        ? this.solveZeroAngle(zeroDistanceYd)
        : launchAngleRad;
      return this._run(angle, maxRangeYd * FT_PER_YARD);
    }

    atRange(zeroDistanceYd, rangeYd) {
      const pts = this.trajectory(zeroDistanceYd, rangeYd + 10.0);
      return interpPointAtRange(pts, rangeYd * FT_PER_YARD);
    }
  }

  // ---------------------------------------------------------- units.py
  const MOA_INCHES_PER_100YD = 100.0 * 3.0 * 12.0 * Math.tan((1.0 / 60.0) * (Math.PI / 180.0));
  const MRAD_INCHES_PER_100YD = 3.6;

  function inchesToMoa(inches, rangeYards) {
    if (rangeYards <= 0) return 0.0;
    return inches / ((MOA_INCHES_PER_100YD * rangeYards) / 100.0);
  }

  function inchesToMrad(inches, rangeYards) {
    if (rangeYards <= 0) return 0.0;
    return inches / ((MRAD_INCHES_PER_100YD * rangeYards) / 100.0);
  }

  function mradToClicks(mrad, clickValueMrad) {
    if (clickValueMrad <= 0) throw new Error("click_value_mrad must be positive");
    return mrad / clickValueMrad;
  }

  // ---------------------------------------------------------- reporting.py
  function reportForPoint(point, clickValueMrad) {
    const r = point.rangeYd;
    const dropMrad = inchesToMrad(point.dropIn, r);
    const windageMrad = inchesToMrad(point.windageIn, r);
    return {
      rangeYd: r,
      dropIn: point.dropIn,
      dropMoa: inchesToMoa(point.dropIn, r),
      dropMrad: dropMrad,
      dropClicks: r > 0 ? mradToClicks(dropMrad, clickValueMrad) : 0.0,
      windageIn: point.windageIn,
      windageMoa: inchesToMoa(point.windageIn, r),
      windageMrad: windageMrad,
      windageClicks: r > 0 ? mradToClicks(windageMrad, clickValueMrad) : 0.0,
      velocityFps: point.velocityFps,
      mach: point.mach,
      timeS: point.timeS,
    };
  }

  // ------------------------------------------------------- public entry point
  // Mirrors api.py's v2_calc_drop_at_range exactly: given a rifle+load
  // (as cached locally) plus atmosphere/wind/range, returns the same
  // shaped report the server would for identical inputs.
  function solveDropAtRange(opts) {
    // Matches api.py's AtmosphereIn.to_conditions() exactly: no station
    // pressure reading means estimate one from altitude rather than
    // silently assuming sea level.
    const pressureInhg = opts.atmosphere.pressure_inhg == null
      ? pressureAtAltitudeInhg(opts.atmosphere.altitude_ft)
      : opts.atmosphere.pressure_inhg;
    const solver = new TrajectorySolver({
      muzzleVelocityFps: opts.load.muzzle_velocity_fps,
      bc: opts.load.bc,
      dragModel: opts.load.drag_model,
      scopeHeightIn: opts.rifle.scope_height_in,
      atmosphere: new AtmosphereConditions(
        opts.atmosphere.temp_f, pressureInhg,
        opts.atmosphere.humidity_pct, opts.atmosphere.altitude_ft
      ),
      wind: new WindCondition(opts.wind.speed_mph, opts.wind.clock_deg),
    });
    const point = solver.atRange(opts.load.zero_distance_yd, opts.rangeYd);
    return reportForPoint(point, opts.rifle.click_value_mrad);
  }

  const BallisticaEngine = {
    AtmosphereConditions, STANDARD_ATMOSPHERE,
    dragCoefficient, pressureAtAltitudeInhg,
    WindCondition, TrajectoryPoint, TrajectorySolver,
    inchesToMoa, inchesToMrad, mradToClicks,
    reportForPoint,
    solveDropAtRange,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = BallisticaEngine;
  } else {
    root.BallisticaEngine = BallisticaEngine;
  }
})(typeof window !== "undefined" ? window : globalThis);
