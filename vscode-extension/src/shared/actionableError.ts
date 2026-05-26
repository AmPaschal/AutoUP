export class ActionableError extends Error {
  readonly actions: readonly string[];

  constructor(message: string, actions: readonly string[]) {
    super(message);
    this.name = "ActionableError";
    this.actions = actions;
  }
}

export function isActionableError(error: unknown): error is ActionableError {
  return error instanceof ActionableError;
}
