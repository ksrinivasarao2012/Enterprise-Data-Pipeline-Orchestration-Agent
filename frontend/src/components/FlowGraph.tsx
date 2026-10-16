import { useId, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import type { FlowEdge, FlowNode, NodeState } from '../lib/flow';
import { useMediaQuery, useReducedMotion } from '../hooks/useMediaQuery';

/**
 * The agent graph drawn as it really branches (CHANGES.md section 34). The main path runs along
 * the top (Monitor → Classifier → Recovery → Verify → Healed); the side paths (RCA, the AI fix
 * and Escalate) sit below. Only the arrows this incident took light up and carry moving
 * "data" particles; a blocked node pulses red.
 */

type Pt = { x: number; y: number };
type Layout = { w: number; h: number; pts: Record<string, Pt>; below: Record<string, boolean>; side: boolean };

// Wide: two rows. Narrow (phones): two lanes, labels beside the circles.
const WIDE: Layout = {
  w: 880,
  h: 392,
  side: false,
  pts: {
    MONITOR_AGENT: { x: 70, y: 110 },
    CLASSIFIER_AGENT: { x: 210, y: 110 },
    RECOVERY_AGENT: { x: 450, y: 110 },
    ACTUATOR: { x: 630, y: 110 },
    FINAL: { x: 800, y: 110 },
    RCA_AGENT: { x: 330, y: 265 },
    AI_FIX_AGENT: { x: 630, y: 265 },
    ESCALATION: { x: 800, y: 265 },
  },
  below: { RCA_AGENT: true, AI_FIX_AGENT: true, ESCALATION: true },
};
const NARROW: Layout = {
  w: 400,
  h: 830,
  side: true,
  pts: {
    MONITOR_AGENT: { x: 40, y: 44 },
    CLASSIFIER_AGENT: { x: 40, y: 150 },
    RCA_AGENT: { x: 200, y: 262 },
    RECOVERY_AGENT: { x: 40, y: 370 },
    ACTUATOR: { x: 40, y: 510 },
    AI_FIX_AGENT: { x: 200, y: 600 },
    FINAL: { x: 40, y: 700 },
    ESCALATION: { x: 200, y: 790 },
  },
  below: {},
};

/** A straight (or gently bent) arrow between two circles, trimmed to their edges. */
function link(a: Pt, b: Pt, shift = 0, bend = 0) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const u = { x: dx / len, y: dy / len };
  const n = { x: -u.y, y: u.x };
  const s = { x: a.x + u.x * 35 + n.x * shift, y: a.y + u.y * 35 + n.y * shift };
  const e = { x: b.x - u.x * 35 + n.x * shift, y: b.y - u.y * 35 + n.y * shift };
  const mid = { x: (s.x + e.x) / 2 + n.x * bend, y: (s.y + e.y) / 2 + n.y * bend };
  const d = bend ? `M ${s.x} ${s.y} Q ${mid.x} ${mid.y} ${e.x} ${e.y}` : `M ${s.x} ${s.y} L ${e.x} ${e.y}`;
  return { d, mid, n };
}

function edgeGeometry(L: Layout, from: string, to: string) {
  const a = L.pts[from];
  const b = L.pts[to];
  // RCA straight to a person: route around the AI fix circle instead of through it.
  if (from === 'RCA_AGENT' && to === 'ESCALATION') {
    if (!L.side) {
      const y = 366;
      return { d: `M ${a.x} ${a.y + 35} C ${a.x} ${y}, ${a.x} ${y}, ${a.x + 40} ${y} L ${b.x - 40} ${y} C ${b.x} ${y}, ${b.x} ${y}, ${b.x} ${b.y + 35}`, mid: { x: 480, y: y + 14 } };
    }
    return { d: `M ${a.x + 24} ${a.y + 24} C ${a.x + 180} ${a.y + 40}, ${b.x + 180} ${b.y - 30}, ${b.x + 24} ${b.y - 24}`, mid: { x: a.x + 150, y: (a.y + b.y) / 2 } };
  }
  // Verify and the AI fix talk both ways: two parallel arrows.
  const pair = (from === 'ACTUATOR' && to === 'AI_FIX_AGENT') || (from === 'AI_FIX_AGENT' && to === 'ACTUATOR');
  const g = link(a, b, pair ? 8 : 0);
  const off = pair ? 24 : 16;
  return { d: g.d, mid: { x: g.mid.x + g.n.x * off, y: g.mid.y + g.n.y * off + 4 } };
}

const STROKE: Record<NodeState, string> = {
  idle: 'stroke-line',
  lit: 'stroke-info',
  blocked: 'stroke-bad',
  skipped: 'stroke-line',
  healed: 'stroke-ok',
  escalated: 'stroke-bad',
  quarantined: 'stroke-warn',
};
const FILL: Record<NodeState, string> = {
  idle: 'fill-surface',
  lit: 'fill-info/15',
  blocked: 'fill-bad/15',
  skipped: 'fill-surface',
  healed: 'fill-ok/15',
  escalated: 'fill-bad/15',
  quarantined: 'fill-warn/15',
};
const TEXT: Record<NodeState, string> = {
  idle: 'fill-faint',
  lit: 'fill-info',
  blocked: 'fill-bad',
  skipped: 'fill-faint',
  healed: 'fill-ok',
  escalated: 'fill-bad',
  quarantined: 'fill-warn',
};
const PARTICLE: Record<NodeState, string> = {
  idle: 'fill-info',
  lit: 'fill-info',
  blocked: 'fill-bad',
  skipped: 'fill-info',
  healed: 'fill-ok',
  escalated: 'fill-bad',
  quarantined: 'fill-warn',
};

const isOn = (s: NodeState) => s !== 'idle' && s !== 'skipped';

interface Props {
  nodes: FlowNode[];
  /** The arrows, with the ones this incident took marked `lit`. */
  edges?: FlowEdge[];
  /** Accessible summary of what the graph shows. */
  label: string;
  /** The circle the user clicked (its details show below the graph). */
  picked?: string | null;
  /** Makes every circle a button; called with the node key (e.g. 'RCA_AGENT'). */
  onPick?: (key: string) => void;
  /** How many times Verify sent the incident back to Recovery: draws a retry arc. */
  loops?: number;
}

export function FlowGraph({ nodes, edges = [], label, picked = null, onPick, loops = 0 }: Props) {
  const wide = useMediaQuery('(min-width: 560px)');
  const reduced = useReducedMotion();
  const uid = useId().replace(/:/g, '');
  const L = wide ? WIDE : NARROW;
  const drawn = edges
    .filter((e) => L.pts[e.from] && L.pts[e.to])
    .map((e) => ({ ...e, ...edgeGeometry(L, e.from, e.to) }));
  const rec = L.pts.RECOVERY_AGENT;
  const ver = L.pts.ACTUATOR;

  return (
    <svg
      viewBox={`0 0 ${L.w} ${L.h}`}
      role={onPick ? 'group' : 'img'}
      aria-label={onPick ? `${label} Click a step to see what it did.` : label}
      className="h-auto w-full select-none overflow-visible"
    >
      <defs>
        <filter id={`glow-${uid}`} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Retry loop: Verify found another problem and sent the incident back to Recovery. */}
      {loops > 0 && (
        <g aria-hidden="true" data-loop={loops}>
          <path
            d={wide
              ? `M ${ver.x - 24} ${ver.y + 24} C ${ver.x - 50} ${ver.y + 62}, ${rec.x + 50} ${rec.y + 62}, ${rec.x + 24} ${rec.y + 24}`
              : `M ${ver.x - 30} ${ver.y - 16} C ${ver.x - 70} ${ver.y - 50}, ${rec.x - 70} ${rec.y + 50}, ${rec.x - 30} ${rec.y + 16}`}
            fill="none"
            className="stroke-info"
            strokeWidth={1.75}
            strokeDasharray="5 5"
          />
          <text
            x={wide ? (rec.x + ver.x) / 2 : rec.x + 44}
            y={wide ? rec.y + 40 : (rec.y + ver.y) / 2 + 4}
            textAnchor={wide ? 'middle' : 'start'}
            className="fill-info font-sans text-[12px] font-medium"
          >
            ↺ retry {loops > 1 ? `×${loops}` : ''}
          </text>
        </g>
      )}

      {drawn.map((e) => (
        <g key={`${e.from}>${e.to}`} data-edge={`${e.from}>${e.to}`} data-lit={e.lit ? 'true' : 'false'}>
          <path d={e.d} fill="none" className="stroke-line" strokeWidth={2} />
          <path
            d={e.d}
            fill="none"
            pathLength={1}
            strokeDasharray="1"
            strokeDashoffset={e.lit ? 0 : 1}
            strokeWidth={2.5}
            strokeLinecap="round"
            className={`${STROKE[e.state]} transition-[stroke-dashoffset] duration-500 ease-out dark:[filter:var(--glow)]`}
            style={{ ['--glow' as string]: `url(#glow-${uid})` }}
          />
          {wide && e.label && (
            <text x={e.mid.x} y={e.mid.y} textAnchor="middle" className={`font-sans text-[11px] ${e.lit ? 'fill-muted' : 'fill-faint'}`}>
              {e.label}
            </text>
          )}
          {e.lit &&
            !reduced &&
            [0, 0.55, 1.1].map((delay) => (
              <circle key={delay} r={3} className={PARTICLE[e.state]} opacity={0}>
                <animateMotion dur="1.6s" begin={`${delay}s`} repeatCount="indefinite" path={e.d} />
                <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.15;0.8;1" dur="1.6s" begin={`${delay}s`} repeatCount="indefinite" />
              </circle>
            ))}
        </g>
      ))}

      {nodes.map((n) => {
        const p = L.pts[n.key];
        if (!p) return null;
        const pulse = !reduced && (n.active || n.state === 'blocked');
        const labelX = L.side ? p.x + 44 : p.x;
        const labelY = L.side ? p.y - 4 : L.below[n.key] ? p.y + 56 : p.y - 62;
        const anchor = L.side ? 'start' : 'middle';
        return (
          <g
            key={n.key}
            data-state={n.state}
            className={`${onPick ? 'group cursor-pointer outline-none' : ''} ${n.state === 'skipped' ? 'opacity-45' : ''}`}
            {...(onPick && {
              role: 'button',
              tabIndex: 0,
              'aria-label': `${n.label}: show what it did`,
              'aria-pressed': picked === n.key,
              'data-node': n.key,
              onClick: () => onPick(n.key),
              onKeyDown: (e: ReactKeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onPick(n.key);
                }
              },
            })}
          >
            {onPick && (
              <circle
                cx={p.x}
                cy={p.y}
                r={40}
                strokeWidth={2}
                strokeDasharray="4 4"
                className={`fill-transparent transition-opacity ${
                  picked === n.key ? 'stroke-fg/60 opacity-100' : 'stroke-focus opacity-0 group-hover:opacity-60 group-focus-visible:opacity-100'
                }`}
              />
            )}
            {pulse && (
              <circle
                cx={p.x}
                cy={p.y}
                r={34}
                fill="none"
                strokeWidth={2}
                className={`${STROKE[n.state === 'idle' ? 'lit' : n.state]} origin-center animate-ring [transform-box:fill-box]`}
              />
            )}
            <circle
              cx={p.x}
              cy={p.y}
              r={32}
              strokeWidth={2}
              strokeDasharray={n.state === 'skipped' ? '4 5' : undefined}
              className={`${FILL[n.state]} ${STROKE[n.state]} transition-colors duration-300 ${isOn(n.state) ? 'dark:[filter:var(--glow)]' : ''}`}
              style={{ ['--glow' as string]: `url(#glow-${uid})` }}
            />
            <circle cx={p.x} cy={p.y} r={24} className="fill-surface" opacity={isOn(n.state) ? 0.55 : 1} />
            <NodeGlyph n={n} x={p.x} y={p.y} />
            <text x={labelX} y={labelY} textAnchor={anchor} className="fill-fg font-sans text-[15px] font-semibold">
              {n.label}
            </text>
            <text x={labelX} y={labelY + 18} textAnchor={anchor} className="fill-muted font-sans text-[12px]">
              {n.sub}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function NodeGlyph({ n, x, y }: { n: FlowNode; x: number; y: number }) {
  const cls = `${TEXT[n.state]} transition-colors duration-300`;
  if (n.state === 'healed') {
    return <path d={`M ${x - 9} ${y} l 6 6 l 12 -13`} fill="none" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" className="stroke-ok" />;
  }
  if (n.state === 'blocked') {
    // Shield with a cross.
    return (
      <g fill="none" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="stroke-bad" transform={`translate(${x - 11} ${y - 12})`}>
        <path d="M11 23s9-4.5 9-11.5V4.5L11 1 2 4.5v7C2 18.5 11 23 11 23z" />
        <path d="M8 9l6 6M14 9l-6 6" />
      </g>
    );
  }
  if (n.num === '!' || n.state === 'escalated') {
    return (
      <text x={x} y={y + 7} textAnchor="middle" className={`${cls} font-display text-[24px]`}>
        !
      </text>
    );
  }
  return (
    <text x={x} y={y + 5} textAnchor="middle" className={`${cls} font-mono text-[14px] font-medium`}>
      {/^\d+$/.test(n.num) ? n.num.padStart(2, '0') : n.num}
    </text>
  );
}
