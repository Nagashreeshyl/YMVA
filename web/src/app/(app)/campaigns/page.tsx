import type { Metadata } from "next";
import { CampaignsView } from "./campaigns-view";

export const metadata: Metadata = { title: "Campaigns" };

export default function CampaignsPage() {
  return <CampaignsView />;
}
