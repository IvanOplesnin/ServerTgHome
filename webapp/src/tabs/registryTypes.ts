import type { ComponentType, ReactElement } from "react";

import type { BootstrapResponse } from "../api/types";

export interface TabComponentProps {
  bootstrap: BootstrapResponse;
}

export interface TabDefinition {
  kind: string;
  title: string;
  icon: (active: boolean) => ReactElement;
  component: ComponentType<TabComponentProps>;
}

export interface ResolvedTab extends TabDefinition {
  id: string;
}
