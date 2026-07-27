export interface ChartPoint {
  x: number;
  y: number;
}

export function buildLinePath(points: Array<ChartPoint | null>): string {
  let drawing = false;
  return points
    .map((point) => {
      if (!point) {
        drawing = false;
        return "";
      }
      const command = drawing ? "L" : "M";
      drawing = true;
      return `${command}${point.x.toFixed(2)},${point.y.toFixed(2)}`;
    })
    .filter(Boolean)
    .join(" ");
}

export function paddedDomain(values: number[], minimumSpan: number): [number, number] {
  if (values.length === 0) {
    return [0, minimumSpan];
  }
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = Math.max(maximum - minimum, minimumSpan);
  const padding = Math.max(span * 0.12, minimumSpan * 0.1);
  const center = (minimum + maximum) / 2;
  return [center - span / 2 - padding, center + span / 2 + padding];
}
