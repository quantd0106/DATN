'use client';
import React, { useId, type ReactNode } from 'react';
import type { DocumentDiagram } from '@/lib/domain';

type MathProps = React.Attributes &
  React.HTMLAttributes<MathMLElement> & { xmlns?: string; mathvariant?: string; width?: string };
declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      math: MathProps;
      mi: MathProps;
      mo: MathProps;
      mn: MathProps;
      mrow: MathProps;
      mfrac: MathProps;
      msqrt: MathProps;
      mtext: MathProps;
      mspace: MathProps;
      msup: MathProps;
      msub: MathProps;
    }
  }
}

const LATEX_SYMBOLS: Record<string, string> = {
  Delta: 'Δ', delta: 'δ', alpha: 'α', beta: 'β', gamma: 'γ', Gamma: 'Γ',
  epsilon: 'ε', varepsilon: 'ε', zeta: 'ζ', eta: 'η', theta: 'θ', Theta: 'Θ',
  kappa: 'κ', lambda: 'λ', Lambda: 'Λ', mu: 'μ', nu: 'ν', xi: 'ξ',
  pi: 'π', Pi: 'Π', rho: 'ρ', sigma: 'σ', Sigma: 'Σ', tau: 'τ',
  phi: 'φ', varphi: 'φ', Phi: 'Φ', chi: 'χ', psi: 'ψ', Psi: 'Ψ',
  omega: 'ω', Omega: 'Ω', infty: '∞',
  neq: '≠', leq: '≤', geq: '≥', le: '≤', ge: '≥', approx: '≈', equiv: '≡', sim: '∼', propto: '∝',
  pm: '±', mp: '∓', div: '÷', cdot: '·', times: '×',
  in: '∈', notin: '∉', subset: '⊂', subseteq: '⊆', cup: '∪', cap: '∩', emptyset: '∅',
  forall: '∀', exists: '∃', nabla: '∇', partial: '∂', sum: '∑', prod: '∏', int: '∫',
  to: '→', rightarrow: '→', leftarrow: '←', leftrightarrow: '↔',
  Rightarrow: '⇒', Leftarrow: '⇐', Leftrightarrow: '⇔', iff: '⇔', implies: '⇒',
  searrow: '↘', nearrow: '↗', perp: '⊥', parallel: '∥', angle: '∠', degree: '°', triangle: '△',
  dots: '…', cdots: '⋯', ldots: '…',
  sin: 'sin', cos: 'cos', tan: 'tan', cot: 'cot', ln: 'ln', log: 'log', lim: 'lim', max: 'max', min: 'min',
  mathbb: '',
};

// A deliberately small, non-HTML math renderer for the supported source notation.
// All strings remain React text; no arbitrary markup/scripts or expression eval.
export function MathFormula({ value }: { value: string }) {
  let index = 0;

  function group(): ReactNode[] {
    while (index < value.length && /\s/.test(value[index])) index++;
    if (value[index] === '{') {
      index++;
      const result = parse('}');
      if (value[index] === '}') index++;
      return result;
    }
    if (value[index] === '\\') {
      return [parseEscape(index)];
    }
    const c = value[index++];
    return c ? [<mi key={`c${index}`}>{c}</mi>] : [];
  }

  function textGroup(): string {
    while (index < value.length && /\s/.test(value[index])) index++;
    if (value[index] !== '{') return value[index++] ?? '';
    index++;
    let out = '';
    let depth = 1;
    while (index < value.length && depth) {
      const c = value[index++];
      if (c === '{') depth++;
      else if (c === '}') depth--;
      if (depth) out += c;
    }
    return out;
  }

  function parseEscape(start: number): ReactNode {
    index++;
    const command = /^[A-Za-z]+/.exec(value.slice(index))?.[0] ?? '';
    index += command.length;
    if (!command) {
      const escaped = value[index++] ?? '';
      if (escaped === '\\') return <mspace key={start} width="0.5em" />;
      if (escaped === ',' || escaped === ';' || escaped === ':' || escaped === ' ') {
        return <mspace key={start} width="0.25em" />;
      }
      if (escaped === '!') return <mspace key={start} width="0px" />;
      return <mo key={start}>{escaped || '\\'}</mo>;
    }
    if (command === 'frac' || command === 'dfrac' || command === 'tfrac') {
      const numerator = group();
      const denominator = group();
      return (
        <mfrac key={start}>
          <mrow>{numerator}</mrow>
          <mrow>{denominator}</mrow>
        </mfrac>
      );
    }
    if (command === 'sqrt') {
      if (value[index] === '[') {
        while (index < value.length && value[index] !== ']') index++;
        if (value[index] === ']') index++;
      }
      return (
        <msqrt key={start}>
          <mrow>{group()}</mrow>
        </msqrt>
      );
    }
    if (command === 'text' || command === 'mathrm' || command === 'mathbf' || command === 'operatorname') {
      return <mtext key={start}>{textGroup()}</mtext>;
    }
    if (command === 'mathbb') {
      return (
        <mi key={start} mathvariant="double-struck">
          {textGroup()}
        </mi>
      );
    }
    if (command === 'left' || command === 'right' || command === 'big' || command === 'Big') {
      while (index < value.length && /\s/.test(value[index])) index++;
      if (value[index] === '.') {
        index++;
        return null;
      }
      if (value[index] === '\\') {
        return parseEscape(start);
      }
      const delim = value[index++];
      return delim ? <mo key={start}>{delim}</mo> : null;
    }
    if (command === 'quad' || command === 'qquad') {
      return <mspace key={start} width="1em" />;
    }
    if (command === 'begin' || command === 'end') {
      const env = textGroup();
      if (command === 'begin' && env === 'cases') return <mo key={start}>{'{'}</mo>;
      return null;
    }
    return <mo key={start}>{LATEX_SYMBOLS[command] ?? command}</mo>;
  }

  function parse(end?: string): ReactNode[] {
    const nodes: ReactNode[] = [];
    while (index < value.length && value[index] !== end) {
      const start = index;
      let node: ReactNode = null;
      const c = value[index++];
      if (c === '\\') {
        index--;
        node = parseEscape(start);
        if (node === null) continue;
      } else if (c === '{') {
        node = <mrow key={start}>{parse('}')}</mrow>;
        if (value[index] === '}') index++;
      } else if (c === '^' || c === '_') {
        const previous = nodes.pop() ?? <mrow />;
        const script = group();
        node =
          c === '^' ? (
            <msup key={start}>
              <mrow>{previous}</mrow>
              <mrow>{script}</mrow>
            </msup>
          ) : (
            <msub key={start}>
              <mrow>{previous}</mrow>
              <mrow>{script}</mrow>
            </msub>
          );
      } else if (/\s/.test(c)) {
        continue;
      } else if (/[0-9.]/.test(c)) {
        let n = c;
        while (index < value.length && /[0-9.]/.test(value[index])) n += value[index++];
        node = <mn key={start}>{n}</mn>;
      } else if (/[A-Za-z]/.test(c)) {
        node = <mi key={start}>{c}</mi>;
      } else {
        node = <mo key={start}>{c}</mo>;
      }
      nodes.push(node);
    }
    return nodes;
  }

  return (
    <math xmlns="http://www.w3.org/1998/Math/MathML" className="reader-math" aria-label={mathText(value)}>
      <mrow>{parse()}</mrow>
    </math>
  );
}

export function mathText(value: string): string {
  return value
    .replace(/\\left|\\right|\\big|\\Big/g, '')
    .replace(/\\(?:frac|dfrac|tfrac)\{([^{}]*)\}\{([^{}]*)\}/g, '($1)/($2)')
    .replace(/\\sqrt\{([^{}]*)\}/g, '√($1)')
    .replace(/\\Delta/g, 'Δ')
    .replace(/\\delta/g, 'δ')
    .replace(/\\alpha/g, 'α')
    .replace(/\\beta/g, 'β')
    .replace(/\\gamma/g, 'γ')
    .replace(/\\pi/g, 'π')
    .replace(/\\theta/g, 'θ')
    .replace(/\\infty/g, '∞')
    .replace(/\\neq/g, '≠')
    .replace(/\\leq/g, '≤')
    .replace(/\\geq/g, '≥')
    .replace(/\\approx/g, '≈')
    .replace(/\\pm/g, '±')
    .replace(/\\times/g, '×')
    .replace(/\\cdot/g, '·')
    .replace(/\\mathbb\{R\}/g, 'ℝ')
    .replace(/\\(?:quad|qquad)/g, ' ')
    .replace(/\\(?:text|mathrm|mathbf)\{([^{}]*)\}/g, '$1')
    .replace(/\\searrow/g, '↘')
    .replace(/\\nearrow/g, '↗')
    .replace(/\\to|\\rightarrow/g, '→')
    .replace(/\\Rightarrow/g, '⇒')
    .replace(/\\Leftrightarrow/g, '⇔')
    .replace(/[{}]/g, '');
}

type MathSegment =
  | { kind: 'text'; value: string }
  | { kind: 'inline'; value: string }
  | { kind: 'display'; value: string };

function splitMathSegments(input: string): MathSegment[] {
  const segments: MathSegment[] = [];
  let cursor = 0;

  const pushText = (chunk: string) => {
    if (!chunk) return;
    const prev = segments[segments.length - 1];
    if (prev && prev.kind === 'text') prev.value += chunk;
    else segments.push({ kind: 'text', value: chunk });
  };

  while (cursor < input.length) {
    if (input.startsWith('$$', cursor)) {
      const end = input.indexOf('$$', cursor + 2);
      if (end > cursor + 2) {
        const expr = input.slice(cursor + 2, end).trim();
        if (expr) {
          segments.push({ kind: 'display', value: expr });
          cursor = end + 2;
          continue;
        }
      }
      pushText('$$');
      cursor += 2;
      continue;
    }

    if (input.startsWith('\\[', cursor)) {
      const end = input.indexOf('\\]', cursor + 2);
      if (end > cursor + 2) {
        const expr = input.slice(cursor + 2, end).trim();
        if (expr) {
          segments.push({ kind: 'display', value: expr });
          cursor = end + 2;
          continue;
        }
      }
    }

    if (input.startsWith('\\(', cursor)) {
      const end = input.indexOf('\\)', cursor + 2);
      if (end > cursor + 2) {
        const expr = input.slice(cursor + 2, end).trim();
        if (expr) {
          segments.push({ kind: 'inline', value: expr });
          cursor = end + 2;
          continue;
        }
      }
    }

    if (input[cursor] === '$' && (cursor === 0 || input[cursor - 1] !== '\\')) {
      let end = cursor + 1;
      let found = -1;
      while (end < input.length) {
        if (input[end] === '\n') break;
        if (input[end] === '$' && input[end - 1] !== '\\') {
          found = end;
          break;
        }
        end++;
      }
      if (found > cursor + 1) {
        const expr = input.slice(cursor + 1, found).trim();
        if (expr) {
          segments.push({ kind: 'inline', value: expr });
          cursor = found + 1;
          continue;
        }
      }
    }

    const nextSpecial = input.slice(cursor + 1).search(/\$|\\[()[\]]/);
    const step = nextSpecial === -1 ? input.length : cursor + 1 + nextSpecial;
    pushText(input.slice(cursor, step));
    cursor = step;
  }

  return segments;
}

function renderHighlightedText(text: string, highlight: string | undefined, keyPrefix: string): ReactNode {
  const needle = highlight?.trim() ?? '';
  if (needle.length < 4) return text;
  const lower = text.toLowerCase();
  const target = needle.toLowerCase();
  const hitIndex = lower.indexOf(target);
  if (hitIndex === -1) return text;
  const before = text.slice(0, hitIndex);
  const matched = text.slice(hitIndex, hitIndex + needle.length);
  const after = text.slice(hitIndex + needle.length);
  return (
    <>
      {before}
      <mark key={`${keyPrefix}-mark`} className="reader-citation-highlight">
        {matched}
      </mark>
      {after}
    </>
  );
}

/**
 * Renders plain text interleaved with LaTeX inline (`$...$`, `\(...\)`) and display (`$$...$$`, `\[...\]`) math
 * via pure React MathML elements without dangerouslySetInnerHTML.
 */
export function RichMathText({ text, highlight }: { text: string; highlight?: string }) {
  const segments = splitMathSegments(text);
  return (
    <>
      {segments.map((seg, idx) => {
        if (seg.kind === 'display') {
          return (
            <span key={idx} className="lumi-math-display">
              <MathFormula value={seg.value} />
            </span>
          );
        }
        if (seg.kind === 'inline') {
          return (
            <span key={idx} className="lumi-math-inline">
              <MathFormula value={seg.value} />
            </span>
          );
        }
        return <React.Fragment key={idx}>{renderHighlightedText(seg.value, highlight, String(idx))}</React.Fragment>;
      })}
    </>
  );
}

function normalizeMatchText(value: string): string {
  return value
    .replace(/\$\$|\\\[|\\\]|\\\(|\\\)|\$|\\/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

/** Checks whether a rendered paragraph or formula contains a cited excerpt (case- and whitespace-insensitive). */
export function quoteMatchesText(haystack: string, quote?: string | null): boolean {
  const cleanQuote = quote?.trim() ?? '';
  if (cleanQuote.length < 4) return false;
  const normHaystack = normalizeMatchText(haystack);
  const normQuote = normalizeMatchText(cleanQuote);
  if (!normQuote || !normHaystack) return false;
  if (normHaystack.includes(normQuote) || normQuote.includes(normHaystack)) return true;
  if (normQuote.length >= 24) {
    return normHaystack.includes(normQuote.slice(0, 24));
  }
  return false;
}

export function ReaderDiagram({ diagram }: { diagram: DocumentDiagram }) {
  const id = useId();
  if (diagram.kind === 'variation')
    return (
      <figure className="reader-diagram">
        <figcaption>{diagram.title}</figcaption>
        <div
          className="reader-variation"
          role="img"
          aria-label={`${diagram.title}: ${diagram.xLabels.join(', ')}; ${diagram.directions.map((d) => (d === 'UP' ? 'đồng biến' : 'nghịch biến')).join(' rồi ')}; ${diagram.extremumLabel}`}
        >
          <div>x</div>
          {diagram.xLabels.map((label, i) => (
            <div key={i}>{label}</div>
          ))}
          <div>y</div>
          <div className="variation-arrow">{diagram.directions[0] === 'UP' ? '↗' : '↘'}</div>
          <div>{diagram.extremumLabel}</div>
          <div className="variation-arrow">{diagram.directions[1] === 'UP' ? '↗' : '↘'}</div>
        </div>
      </figure>
    );
  const up = diagram.direction === 'UP';
  const vertexY = up ? 118 : 76;
  return (
    <figure className={`reader-diagram ${up ? 'up' : 'down'}`}>
      <figcaption>{diagram.title}</figcaption>
      <svg viewBox="0 0 320 210" role="img" aria-labelledby={`${id}-title ${id}-description`}>
        <title id={`${id}-title`}>{diagram.title}</title>
        <desc id={`${id}-description`}>
          Sơ đồ định tính của parabol. {diagram.vertexLabel}. Trục đối xứng {diagram.axisLabel}. Không theo tỉ lệ.
        </desc>
        <defs>
          <marker id={`${id}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M0 0 10 5 0 10Z" fill="#64748b" />
          </marker>
        </defs>
        <path d="M32 168H298 M78 188V20" stroke="#94a3b8" strokeWidth="1.5" markerEnd={`url(#${id}-arrow)`} />
        <text x="300" y="181">x</text>
        <text x="64" y="19">y</text>
        <text x="64" y="182">O</text>
        <path d={up ? 'M40 40 Q160 196 280 40' : 'M40 154 Q160 -2 280 154'} fill="none" stroke={up ? '#2563eb' : '#c2410c'} strokeWidth="3" />
        <path d="M160 24V186" stroke="#2563eb" strokeDasharray="4 4" />
        <circle cx="160" cy={vertexY} r="4" fill={up ? '#2563eb' : '#c2410c'} />
        <text x="171" y={vertexY - 10} className="diagram-vertex">I</text>
        <text x="165" y="201" className="diagram-axis">{diagram.axisLabel}</text>
      </svg>
      <p>{diagram.vertexLabel}</p>
      <small>Sơ đồ định tính, không theo tỉ lệ.</small>
    </figure>
  );
}
