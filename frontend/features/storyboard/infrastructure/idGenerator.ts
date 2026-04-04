import type { IdGenerator } from '../application/ports';

export const uuidGenerator: IdGenerator = {
  next: () => crypto.randomUUID(),
};
