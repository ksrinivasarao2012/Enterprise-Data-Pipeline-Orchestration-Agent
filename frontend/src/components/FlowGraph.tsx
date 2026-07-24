import { useId, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import type { FlowNode, NodeState } from '../lib/flow';
import { useMediaQuery, useReducedMotion } from '../hooks/useMediaQuery';

/**
 * The agent flow drawn as a loop: Monitor → Classifier → RCA along the top,
 * down to Recovery, then back along the bottom through Verify to the outcome.
 * Lit edges carry moving "data" particles; a blocked node pulses red.
 */

type Pt = { x: number; y: number };

// Wide: a U-shaped loop. Narrow: a single column.
const WIDE = {
  w: 780,
  h: 330,
  pts: [
    { x: 110, y: 80 },
    { x: 390, y: 80 },
    { x: 670, y: 80 },
    { x: 670, y: 245 },
    { x: 390, y: 245 },
    { x: 110, y: 245 },
  ] as Pt[],
};
const NARROW = {
  w: 340,
  h: 610,
  pts: [0, 1, 2, 3, 4, 5].map((i) => ({ x: 52, y: 50 + i * 102 })) as Pt[],
};

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

function edgePath(a: Pt, b: Pt, wide: boolean, i: number): string {
  // The turn from RCA down to Recovery bows outwards on the wide layout.
  if (wide && i === 2) return `M ${a.x + 34} ${a.y} C ${a.x + 110} ${a.y}, ${b.x + 110} ${b.y}, ${b.x + 34} ${b.y}`;
  if (wide) {
    const dir = b.x > a.x ? 1 : -1;
    return `M ${a.x + dir * 36} ${a.y} L ${b.x - dir * 36} ${b.y}`;
  }
  return `M ${a.x} ${a.y + 36} L ${b.x} ${b.y - 36}`;
}

const isOn = (s: NodeState) => s !== 'idle' && s !== 'skipped';

interface Props {
  nodes: FlowNode[];
  /** Accessible summary of what the graph shows. */
  label: string;
  /** The circle the user clicked (its details show below the graph). */
  picked?: string | null;
  /** Makes every circle a button; called with the node key (e.g. 'RCA_AGENT'). */
  onPick?: (key: string) => void;
  /** How many times Verify sent the incident back to Recovery: draws a retry arc. */
  loops?: number;
}

export function FlowGraph({ nodes, label, picked = null, onPick, loops = 0 }: Props) {
  const wide = useMediaQuery('(min-width: 560px)');
  const reduced = useReducedMotion();
  const uid = useId().replace(/:/g, '');
  const L = wide ? WIDE : NARROW;

  // The graph branches, so a finished run can skip steps (e.g. no RCA when a rule knew the fix).
  // The lit path runs from the first to the last step that ran and passes over skipped ones.
  const onIdx = nodes.map((n, i) => (isOn(n.state) ? i : -1)).filter((i) => i >= 0);
  const firstOn = onIdx.length ? onIdx[0] : Infinity;
  const lastOn = onIdx.length ? onIdx[onIdx.length - 1] : -1;
  const edges = L.pts.slice(0, -1).map((a, i) => {
    const from = nodes[i];
    const to = nodes[i + 1];
    const lit = firstOn <= i && lastOn >= i + 1 && from.state !== 'idle' && to.state !== 'idle';
    return { d: edgePath(a, L.pts[i + 1], wide, i), lit, state: from.state === 'blocked' ? 'blocked' : to.state === 'healed' ? 'healed' : to.state === 'escalated' ? 'escalated' : 'lit' } as const;
  });

  // Faint return path from the outcome back to Monitor: the "loop" in self-healing.
  const back = wide
    ? `M ${L.pts[5].x - 34} ${L.pts[5].y} C ${L.pts[5].x - 110} ${L.pts[5].y}, ${L.pts[0].x - 110} ${L.pts[0].y}, ${L.pts[0].x - 34} ${L.pts[0].y}`
    : null;

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

      {back && <path d={back} fill="none" className="stroke-line" strokeWidth={1.5} strokeDasharray="3 6" />}

      {/* Retry loop: Verify found another problem and sent the incident back to Recovery. */}
      {wide && loops > 0 && (
        <g aria-hidden="true" data-loop={loops}>
          <path
            d={`M ${L.pts[4].x + 26} ${L.pts[4].y - 24} C ${L.pts[4].x + 90} ${L.pts[4].y - 78}, ${L.pts[3].x - 90} ${L.pts[3].y - 78}, ${L.pts[3].x - 26} ${L.pts[3].y - 24}`}
            fill="none"
            className="stroke-info"
            strokeWidth={1.75}
            strokeDasharray="5 5"
          />
          <text x={(L.pts[3].x + L.pts[4].x) / 2} y={L.pts[3].y - 66} textAnchor="middle" className="fill-info font-sans text-[12px] font-medium">
            ↺ retry {loops > 1 ? `×${loops}` : ''}
          </text>
        </g>
      )}

      {edges.map((e, i) => (
        <g key={i}>
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

      {nodes.map((n, i) => {
        const p = L.pts[i];
        const pulse = !reduced && (n.active || n.state === 'blocked');
        const labelX = wide ? p.x : p.x + 52;
        const labelY = wide ? (i < 3 ? p.y - 62 : p.y + 58) : p.y - 4;
        const anchor = wide ? 'middle' : 'start';
        return (
          <g
            key={n.key}
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
  if (n.state === 'escalated') {
    return (
      <text x={x} y={y + 7} textAnchor="middle" className={`${cls} font-display text-[24px]`}>
        !
      </text>
    );
  }
  return (
    <text x={x} y={y + 5} textAnchor="middle" className={`${cls} font-mono text-[14px] font-medium`}>
      {n.num.padStart(2, '0')}
    </text>
  );
}
