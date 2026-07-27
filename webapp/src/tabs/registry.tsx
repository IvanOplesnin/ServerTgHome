import { CameraIcon, ClimateIcon } from "../components/Icons";
import { CamerasTab } from "./CamerasTab";
import { ClimateTab } from "./ClimateTab";
import type { ResolvedTab, TabDefinition } from "./registryTypes";
import type { TabConfig } from "../api/types";

const registry = new Map<string, TabDefinition>();

export function registerTab(definition: TabDefinition): void {
  registry.set(definition.kind, definition);
}

registerTab({
  kind: "cameras",
  title: "Камеры",
  icon: () => <CameraIcon />,
  component: CamerasTab,
});

registerTab({
  kind: "climate",
  title: "Климат",
  icon: () => <ClimateIcon />,
  component: ClimateTab,
});

const defaultTabs: TabConfig[] = [
  { id: "cameras", kind: "cameras", order: 10 },
  { id: "climate", kind: "climate", order: 20 },
];

export function resolveTabs(config?: Array<TabConfig | string>): ResolvedTab[] {
  const source = config?.length ? config : defaultTabs;
  return source
    .map((raw, index) => {
      const item: TabConfig =
        typeof raw === "string" ? { id: raw, kind: raw, order: index } : raw;
      if (item.enabled === false) {
        return undefined;
      }
      const definition = registry.get(item.kind ?? item.id);
      if (!definition) {
        return undefined;
      }
      return {
        ...definition,
        id: item.id,
        title: item.title || definition.title,
        order: item.order ?? index,
      };
    })
    .filter(
      (
        tab,
      ): tab is ResolvedTab & {
        order: number;
      } => Boolean(tab),
    )
    .sort((left, right) => left.order - right.order)
    .map(({ order: _order, ...tab }) => tab);
}
