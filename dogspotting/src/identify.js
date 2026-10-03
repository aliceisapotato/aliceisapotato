// Breed identification: sends the photo to Claude's vision model and gets back
// structured JSON describing the dog (or explaining why it isn't one).
import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";

const client = new Anthropic(); // reads ANTHROPIC_API_KEY from the environment

export const RARITIES = ["common", "uncommon", "rare", "legendary"];

// The exact shape we want back. Structured outputs guarantee the reply
// matches this schema, so the game never has to scrape free text.
const Identification = z.object({
  is_dog: z.boolean().describe("True only if a real dog is clearly visible"),
  is_live_photo: z
    .boolean()
    .describe(
      "False if this looks like a photo of a screen, printout, poster, toy, or statue rather than a dog in front of the camera",
    ),
  breed: z
    .string()
    .describe('Most likely breed, e.g. "Golden Retriever". Use "Mixed Breed" if no single breed dominates. Empty string if not a dog.'),
  possible_mix: z
    .array(z.string())
    .describe("Breeds likely in the mix, most likely first. Empty if purebred-looking."),
  confidence: z.number().describe("0.0-1.0 confidence in the breed call"),
  rarity: z
    .enum(RARITIES)
    .describe(
      "How unusual it is to spot this breed on a typical city street: common (labs, goldens, doodles), uncommon, rare, legendary (e.g. Azawakh, Lagotto, Mudi)",
    ),
  attributes: z.object({
    size: z.string().describe("toy / small / medium / large / giant"),
    coat: z.string().describe("Coat length and texture"),
    colors: z.array(z.string()),
    ears: z.string(),
    tail: z.string(),
    distinguishing_features: z.array(z.string()),
  }),
  reasoning: z.string().describe("One or two sentences: which visible attributes led to this breed"),
  fun_fact: z.string().describe("A short, friendly fact about the breed"),
});

const SYSTEM_PROMPT = `You are the breed scanner in Dogspotting, a game where players photograph dogs they meet on the street and collect breeds like a Pokédex.

Look carefully at the dog's visible attributes - head shape, muzzle length, ear set, coat type and color, body proportions, tail carriage, size relative to surroundings - and identify the most likely breed.

Be honest about uncertainty: if the dog is partly hidden, blurry, a puppy, or clearly a mix, lower your confidence and use "Mixed Breed" with possible_mix rather than guessing a rare breed. Never inflate rarity; rarity should reflect how often the breed is actually seen in public.

If there is no real dog in the photo, set is_dog to false and leave breed empty. If there are several dogs, describe the most prominent one.`;

/**
 * @param {Buffer} imageBuffer  JPEG/PNG/WebP/GIF bytes (keep under ~5 MB)
 * @param {string} mediaType    e.g. "image/jpeg"
 */
export async function identifyDog(imageBuffer, mediaType) {
  const format = zodOutputFormat(Identification);

  const response = await client.beta.messages.create({
    model: "claude-opus-5-5",
    max_tokens: 4000,
    // If a safety classifier ever declines a photo, retry on Anthropic's
    // recommended fallback model instead of failing the catch.
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    output_config: { effort: "low", format },
    system: SYSTEM_PROMPT,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "image",
            source: { type: "base64", media_type: mediaType, data: imageBuffer.toString("base64") },
          },
          { type: "text", text: "Scan this dog." },
        ],
      },
    ],
  });

  if (response.stop_reason === "refusal") {
    throw new Error("The scanner declined to analyze this photo.");
  }
  if (response.stop_reason === "max_tokens") {
    throw new Error("The scanner's answer was cut off. Try again.");
  }

  const text = response.content.find((block) => block.type === "text")?.text;
  if (!text) throw new Error("The scanner returned no result.");

  const result = format.parse(text);
  result.confidence = Math.min(1, Math.max(0, result.confidence));
  return result;
}
