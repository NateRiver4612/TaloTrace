export const queryKeys = {
  interview: (token: string) => ["interview", token] as const,
  roles: () => ["roles"] as const,
  candidates: (roleId: string) => ["candidates", roleId] as const,
  worker: () => ["worker"] as const,
};
