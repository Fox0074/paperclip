/** Public onboarding metadata. Credentials and broker settings stay server-side. */
export interface ExecutionPreset {
  id: string;
  label: string;
  adapterType: "opencode_local";
  defaultModel: string;
  models: { id: string; label: string }[];
}
