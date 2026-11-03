import { z } from "zod";

export const loginSchema = z.object({
  username: z.string().min(1, "Username is required."),
  password: z.string().min(1, "Password is required."),
});
export type LoginInput = z.infer<typeof loginSchema>;

export const overrideSchema = z.object({
  score: z.number().int().min(1).max(4),
  reason: z.string().trim().min(1, "A reason is required.").max(2000, "Reason is too long."),
});
export type OverrideInput = z.infer<typeof overrideSchema>;

export const answerSchema = z
  .object({
    mode: z.enum(["text", "audio"]),
    text: z.string(),
    audio: z.custom<File | null>(),
  })
  .superRefine((values, ctx) => {
    if (values.mode === "text" && !values.text.trim()) {
      ctx.addIssue({ code: "custom", path: ["text"], message: "Please type your answer." });
    }
    if (values.mode === "audio" && !values.audio) {
      ctx.addIssue({ code: "custom", path: ["audio"], message: "Please choose an audio file." });
    }
  });
export type AnswerInput = z.infer<typeof answerSchema>;
