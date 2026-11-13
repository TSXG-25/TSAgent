import { SCENES } from "../../app/scenes";
import { t, type I18nKey } from "../../app/i18n";

/** Omni empty-conversation landing — DeepSeek-style centered entry.
 *  Every suggestion is a real route: the text is prefilled into the
 *  composer (tsagent:composer-draft) and submitting it runs the
 *  @planet delegation, same as typing it by hand. */

const SUGGESTED = ["mercury", "venus", "mars", "earth", "uranus", "saturn"] as const;

const accentOf = (planet: string) => SCENES.find((s) => s.planet === planet)?.accent ?? "var(--planet-accent)";

function prefill(prompt: string) {
  window.dispatchEvent(new CustomEvent("tsagent:composer-draft", { detail: { text: prompt } }));
}

export default function OmniLanding() {
  return (
    <div className="omni-landing">
      <span className="omni-landing-mark">⬡</span>
      <h3 className="omni-landing-title">{t("landing.headline")}</h3>
      <p className="omni-landing-sub">{t("landing.sub")}</p>
      <div className="omni-landing-grid">
        {SUGGESTED.map((p) => (
          <button className="omni-landing-chip" key={p} onClick={() => prefill(t(`landing.sug.${p}.prompt` as I18nKey))} type="button">
            <span className="landing-chip-head">
              <i className="landing-dot" style={{ background: accentOf(p) }} />
              <strong>{t(`landing.sug.${p}.label` as I18nKey)}</strong>
              <em>@{p}</em>
            </span>
            <small>{t(`landing.sug.${p}.hint` as I18nKey)}</small>
          </button>
        ))}
      </div>
      <p className="omni-landing-foot">{t("landing.hint")}</p>
    </div>
  );
}
