> ## Documentation Index
> Fetch the complete documentation index at: https://docs.cartesia.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Sonic 3.6

<Note>
  Sonic 3.6 is now Generally Available! Try it on the [Playground](https://play.cartesia.ai/text-to-speech?model_id=sonic-3.6) or via the API now!
</Note>

Sonic 3.6 (`sonic-3.6`) is our fastest, most natural text-to-speech model, with native support for 44 languages. It follows your transcript faithfully, voices confirmation codes and heteronyms correctly without preprocessing, and stays expressive enough to carry a real conversation.

## What's new in Sonic 3.6?

* **Human-like naturalness** — Sonic 3.6 is the most natural and human-sounding TTS model. It sounds more natural, more expressive, and better paced across every language. It varies its pacing and intonation to match the emotional context of the transcript, without SSML tags or explicit instructions: pause length adapts to the context of the sentence, and in-transcript disfluencies (e.g. "uhm", "hmm") produce a natural "thinking" pace.
* **Native speaker quality** — Sonic 3.6 speaks 44 languages and pronounces words and phrases the way a local would.
* **Hinglish support** — Sonic 3.6 has expanded support for Hindi transcripts written in Latin script, as well as improved pronunciation of Indian names and places. See our [dedicated guide](/build-with-cartesia/capability-guides/advanced-capabilities#romanized-hindi-and-indic-text) for more details.
* **Two new languages** — Odia (`or`) and Urdu (`ur`), with instant voice cloning support. See [language support](#language-support).

<Note>
  PVCs created on older models will work on Sonic 3.6.
</Note>

## How can I use it?

### Continuous updates (Recommended)

Using the model ID `sonic-3.6` in your API calls will automatically keep you up to date with the most recent stable snapshot of the model.

For testing upcoming changes to our APIs, use `sonic-preview`. It is a beta model, not intended for production usage, and can change without notice.

| `model_id`             | Model update behavior                                       | Recommended for                                                                            |
| ---------------------- | :---------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `sonic-3.6-YYYY-MM-DD` | Snapshotted, will never change                              | Customers who want to run internal evals before any updates                                |
| `sonic-3.6`            | Will be updated to point to the most recent stable snapshot | Customers who want stable releases, but want to be up-to-date with the recent capabilities |
| `sonic-preview`        | Beta model, will always be updated with upcoming changes    | Testing purposes                                                                           |

### Dated snapshots

If ensuring consistent behavior is important for your use case, we recommend using a dated version in production. A dated snapshot never changes once released.

| Snapshot               | Release Date    | Languages                                                                                                                                                                      | Status                              |
| ---------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------- |
| `sonic-3.6-2026-08-27` | August 27, 2026 | en, de, es, fr, ja, pt, zh, hi, ko, it, nl, pl, ru, sv, tr, tl, bg, ro, ar, cs, el, fi, hr, ms, sk, da, ta, uk, hu, no, vi, bn, th, he, ka, id, te, gu, kn, ml, mr, pa, or, ur | <Badge color="green">Stable</Badge> |

## Voice selection

Choosing voices that work best for your use case is key to getting the best performance out of Sonic 3.6. For voice agents, we've found stable, pleasant, yet also realistic voices work better for voice agents. Some voices you should try include:

* Skylar - `en-US` Female (ID: `db6b0ed5-d5d3-463d-ae85-518a07d3c2b4`)
* Daniel - `en-US` Male (ID: `47c38ca4-5f35-497b-b1a3-415245fb35e1`)
* Jacqueline - `en-US` Female (ID: `9626c31c-bec5-4cca-baa8-f8ba9e84c8bc`)
* Gemma - `en-GB` Female (ID: `62ae83ad-4f6a-430b-af41-a9bede9286ca`)
* Archie - `en-GB` Male (ID: `ef191366-f52f-447a-a398-ed8c0f2943a1`)

For more information and recommendations, see [Choosing a Voice](/build-with-cartesia/capability-guides/choosing-a-voice), or take a look at the featured voices on our [Voice Library](https://play.cartesia.ai/voices).

## Language support

Sonic 3.6 speaks 44 languages. Set the `language` field on your TTS request to one of the codes below:

<table>
  <tbody>
    <tr>
      <td>English (`en`)</td>
      <td>French (`fr`)</td>
      <td>German (`de`)</td>
      <td>Spanish (`es`)</td>
    </tr>

    <tr>
      <td>Portuguese (`pt`)</td>
      <td>Chinese (`zh`)</td>
      <td>Japanese (`ja`)</td>
      <td>Hindi (`hi`)</td>
    </tr>

    <tr>
      <td>Italian (`it`)</td>
      <td>Korean (`ko`)</td>
      <td>Dutch (`nl`)</td>
      <td>Polish (`pl`)</td>
    </tr>

    <tr>
      <td>Russian (`ru`)</td>
      <td>Swedish (`sv`)</td>
      <td>Turkish (`tr`)</td>
      <td>Tagalog (`tl`)</td>
    </tr>

    <tr>
      <td>Bulgarian (`bg`)</td>
      <td>Romanian (`ro`)</td>
      <td>Arabic (`ar`)</td>
      <td>Czech (`cs`)</td>
    </tr>

    <tr>
      <td>Greek (`el`)</td>
      <td>Finnish (`fi`)</td>
      <td>Croatian (`hr`)</td>
      <td>Malay (`ms`)</td>
    </tr>

    <tr>
      <td>Slovak (`sk`)</td>
      <td>Danish (`da`)</td>
      <td>Tamil (`ta`)</td>
      <td>Ukrainian (`uk`)</td>
    </tr>

    <tr>
      <td>Hungarian (`hu`)</td>
      <td>Norwegian (`no`)</td>
      <td>Vietnamese (`vi`)</td>
      <td>Bengali (`bn`)</td>
    </tr>

    <tr>
      <td>Thai (`th`)</td>
      <td>Hebrew (`he`)</td>
      <td>Georgian (`ka`)</td>
      <td>Indonesian (`id`)</td>
    </tr>

    <tr>
      <td>Telugu (`te`)</td>
      <td>Gujarati (`gu`)</td>
      <td>Kannada (`kn`)</td>
      <td>Malayalam (`ml`)</td>
    </tr>

    <tr>
      <td>Marathi (`mr`)</td>
      <td>Punjabi (`pa`)</td>
      <td>Odia (`or`)</td>
      <td>Urdu (`ur`)</td>
    </tr>
  </tbody>
</table>

<Note>
  Odia and Urdu are new in Sonic 3.6. Curated voices for both are in the [Voice Library](https://play.cartesia.ai/voices), or [clone a voice](/build-with-cartesia/capability-guides/clone-voices) in either language.
</Note>

<Note>
  Sonic 3.6 now supports locale codes using the `locale` field in the API, so pass `en-GB` if you want 05/04/2026 to be read as "the fifth of April, twenty twenty-six.". For more information, see [Advanced Capabilities](/build-with-cartesia/capability-guides/advanced-capabilities#locale-codes).
</Note>

## Switching from Sonic 3.5?

Note that Sonic 3.6 is fully backwards compatible with Sonic 3.5.

## Older Models

For information on `sonic-3`, `sonic-2`, `sonic-turbo`, and `sonic`, see our page on [Older TTS Models](/build-with-cartesia/tts-models/older-models).

## Where to go next

<CardGroup cols={2}>
  <Card title="Try it out online" icon="arrow-pointer" href="https://www.cartesia.ai/sonic">
    No sign-up or code required
  </Card>

  <Card title="Start building" icon="readme" href="/build-with-cartesia/capability-guides/tts">
    Guides and best practices
  </Card>
</CardGroup>
