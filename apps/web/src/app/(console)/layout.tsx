import type { ReactNode } from "react";
import { LiveProvider } from "@/components/Live";
import { Shell } from "@/components/Shell";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <LiveProvider>
      <Shell>{children}</Shell>
    </LiveProvider>
  );
}
