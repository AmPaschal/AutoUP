import type { AutoUpConfig, ModelProfile, ResolvedModelProfile } from "../core/types";

export const GLOBAL_ENV_SECRET_PREFIX = "autoup.env.";

export function getEnvironmentSecretKey(envVarName: string): string {
  return `${GLOBAL_ENV_SECRET_PREFIX}${envVarName}`;
}

export function normalizeEnvironmentVariableName(value: string): string {
  return value.trim();
}

export function normalizeEnvironmentVariableList(envVars: readonly string[]): string[] {
  const normalized: string[] = [];
  const seen = new Set<string>();
  for (const envVar of envVars) {
    const trimmed = normalizeEnvironmentVariableName(envVar);
    if (!trimmed || seen.has(trimmed)) {
      continue;
    }
    seen.add(trimmed);
    normalized.push(trimmed);
  }
  return normalized;
}

export function normalizeModelProfiles(value: unknown): ModelProfile[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") {
      return [];
    }
    const profile = entry as Partial<ModelProfile>;
    const name = typeof profile.name === "string" ? profile.name.trim() : "";
    const provider = typeof profile.provider === "string" ? profile.provider.trim() : "";
    const modelName = typeof profile.modelName === "string" ? profile.modelName.trim() : "";
    const envVars = Array.isArray(profile.envVars)
      ? normalizeEnvironmentVariableList(profile.envVars.filter((item): item is string => typeof item === "string"))
      : [];
    return [{
      name,
      provider,
      modelName,
      envVars,
    }];
  });
}

export function resolveSelectedModelProfile(config: AutoUpConfig): ResolvedModelProfile {
  const selectedName = config.selectedModelProfile?.trim();
  if (!selectedName) {
    throw new Error("No AutoUP model profile is selected. Configure autoup.selectedModelProfile in settings.");
  }

  const profilesByName = new Map<string, ModelProfile>();
  for (const profile of config.modelProfiles) {
    if (!profile.name) {
      continue;
    }
    if (profilesByName.has(profile.name)) {
      throw new Error(`Duplicate AutoUP model profile name '${profile.name}' found in settings.`);
    }
    profilesByName.set(profile.name, profile);
  }

  const selected = profilesByName.get(selectedName);
  if (!selected) {
    throw new Error(`Selected AutoUP model profile '${selectedName}' was not found in autoup.modelProfiles.`);
  }
  if (!selected.provider) {
    throw new Error(`AutoUP model profile '${selected.name}' is missing a provider.`);
  }
  if (!selected.modelName) {
    throw new Error(`AutoUP model profile '${selected.name}' is missing a modelName.`);
  }

  const envVars = normalizeEnvironmentVariableList(selected.envVars);
  return {
    name: selected.name,
    provider: selected.provider,
    modelName: selected.modelName,
    envVars,
  };
}
