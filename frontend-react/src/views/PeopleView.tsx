import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type {
  MemberProfileResult,
  ProfessionalProfile,
  PublicProfileResult,
} from "../lib/types";

type Props = {
  t: (key: string) => string;
  onToast: (message: string, error?: boolean) => void;
};

const asLines = (values: string[]) => values.join("\n");
const parseLines = (value: string) =>
  Array.from(new Set(value.split(/[\n,;]/).map((part) => part.trim()).filter(Boolean)));

export function PeopleView({ t, onToast }: Props) {
  const [profile, setProfile] = useState<ProfessionalProfile | null>(null);
  const [courses, setCourses] = useState("");
  const [qualifications, setQualifications] = useState("");
  const [skills, setSkills] = useState("");
  const [phone, setPhone] = useState("");
  const [discoverable, setDiscoverable] = useState(false);
  const [shareEmail, setShareEmail] = useState(false);
  const [sharePhone, setSharePhone] = useState(false);
  const [loadingProfile, setLoadingProfile] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);
  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] = useState<"members" | "web">("members");
  const [members, setMembers] = useState<MemberProfileResult[]>([]);
  const [publicProfiles, setPublicProfiles] = useState<PublicProfileResult[]>([]);
  const [publicNotice, setPublicNotice] = useState("");
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);

  useEffect(() => {
    let active = true;
    void api<ProfessionalProfile>("/api/people/profile")
      .then((result) => {
        if (!active) return;
        setProfile(result);
        setCourses(asLines(result.courses));
        setQualifications(asLines(result.qualifications));
        setSkills(asLines(result.skills));
        setPhone(result.phone);
        setDiscoverable(result.discoverable);
        setShareEmail(result.share_email);
        setSharePhone(result.share_phone);
      })
      .catch((error: unknown) => onToast(error instanceof Error ? error.message : String(error), true))
      .finally(() => {
        if (active) setLoadingProfile(false);
      });
    return () => { active = false; };
  }, [onToast]);

  const saveProfile = async (event: React.FormEvent) => {
    event.preventDefault();
    setSavingProfile(true);
    try {
      const saved = await api<ProfessionalProfile>("/api/people/profile", {
        method: "PUT",
        body: {
          courses: parseLines(courses),
          qualifications: parseLines(qualifications),
          skills: parseLines(skills),
          phone: phone.trim(),
          discoverable,
          share_email: shareEmail,
          share_phone: sharePhone,
        },
      });
      setProfile(saved);
      onToast(t("profileSaved"));
    } catch (error) {
      onToast(error instanceof Error ? error.message : String(error), true);
    } finally {
      setSavingProfile(false);
    }
  };

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    const term = query.trim();
    if (term.length < 2) {
      onToast(t("peopleSearchTooShort"), true);
      return;
    }
    setSearching(true);
    setHasSearched(true);
    setMembers([]);
    setPublicProfiles([]);
    setPublicNotice("");
    try {
      if (searchMode === "members") {
        const result = await api<{ results: MemberProfileResult[] }>(
          `/api/people/search?query=${encodeURIComponent(term)}`,
        );
        setMembers(result.results);
      } else {
        const result = await api<{
          results: PublicProfileResult[];
          notice: string;
        }>(`/api/people/search/external?skill=${encodeURIComponent(term)}`);
        setPublicProfiles(result.results);
        setPublicNotice(result.notice);
      }
    } catch (error) {
      onToast(error instanceof Error ? error.message : String(error), true);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="people-page">
      <header className="people-header">
        <div>
          <h2>{t("peopleTitle")}</h2>
          <p>{t("peopleDescription")}</p>
        </div>
      </header>

      <section className="people-panel">
        <h3>{t("professionalProfile")}</h3>
        <p className="people-help">{t("profilePrivacyHint")}</p>
        {loadingProfile ? <p>{t("loading")}</p> : (
          <form onSubmit={saveProfile}>
            <label className="people-field">
              <span>{t("coursesStudied")}</span>
              <textarea value={courses} onChange={(event) => setCourses(event.target.value)} rows={2} placeholder={t("onePerLine")} />
            </label>
            <label className="people-field">
              <span>{t("qualifications")}</span>
              <textarea value={qualifications} onChange={(event) => setQualifications(event.target.value)} rows={2} placeholder={t("onePerLine")} />
            </label>
            <label className="people-field">
              <span>{t("skills")}</span>
              <textarea value={skills} onChange={(event) => setSkills(event.target.value)} rows={3} placeholder={t("onePerLine")} />
            </label>
            <div className="people-sharing">
              <label className="people-check">
                <input
                  type="checkbox"
                  checked={discoverable}
                  onChange={(event) => {
                    setDiscoverable(event.target.checked);
                    if (!event.target.checked) {
                      setShareEmail(false);
                      setSharePhone(false);
                    }
                  }}
                />
                <span>{t("profileDiscoverable")}</span>
              </label>
              <label className="people-check">
                <input type="checkbox" checked={shareEmail} onChange={(event) => setShareEmail(event.target.checked)} disabled={!discoverable} />
                <span>{t("shareProfileEmail")} {profile?.email ? `(${profile.email})` : ""}</span>
              </label>
              <label className="people-check">
                <input type="checkbox" checked={sharePhone} onChange={(event) => setSharePhone(event.target.checked)} disabled={!discoverable} />
                <span>{t("shareProfilePhone")}</span>
              </label>
              {sharePhone && discoverable && (
                <label className="people-field">
                  <span>{t("phoneNumber")}</span>
                  <input value={phone} onChange={(event) => setPhone(event.target.value)} type="tel" maxLength={50} />
                </label>
              )}
            </div>
            <div className="people-form-actions">
              <button className="btn primary" type="submit" disabled={savingProfile || loadingProfile}>
                {savingProfile ? t("saving") : t("saveProfile")}
              </button>
            </div>
          </form>
        )}
      </section>

      <section className="people-panel">
        <h3>{t("findPeople")}</h3>
        <div className="people-tabs" role="tablist">
          <button type="button" className={`btn small ${searchMode === "members" ? "primary" : ""}`} onClick={() => setSearchMode("members")}>
            {t("jaloraMembers")}
          </button>
          <button type="button" className={`btn small ${searchMode === "web" ? "primary" : ""}`} onClick={() => setSearchMode("web")}>
            {t("publicWebProfiles")}
          </button>
        </div>
        <form className="people-search" onSubmit={search}>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("searchSkillPlaceholder")} minLength={2} maxLength={120} />
          <button className="btn primary" type="submit" disabled={searching}>
            {searching ? t("searching") : t("searchLabel")}
          </button>
        </form>
        {searchMode === "web" && <p className="people-help">{t("publicSearchNotice")}</p>}

        {hasSearched && !searching && searchMode === "members" && members.length === 0 && (
          <p className="people-empty">{t("noPeopleFound")}</p>
        )}
        {members.length > 0 && (
          <div className="people-results">
            {members.map((person) => (
              <article className="people-result" key={person.id}>
                <h4>{person.name || t("unnamedMember")}</h4>
                {person.skills.length > 0 && <p><strong>{t("skills")}:</strong> {person.skills.join(", ")}</p>}
                {person.qualifications.length > 0 && <p><strong>{t("qualifications")}:</strong> {person.qualifications.join(", ")}</p>}
                {person.courses.length > 0 && <p><strong>{t("coursesStudied")}:</strong> {person.courses.join(", ")}</p>}
                <div className="people-contact-actions">
                  {person.email && <a className="btn small" href={`mailto:${person.email}`}>{t("emailPerson")}</a>}
                  {person.phone && <a className="btn small" href={`tel:${person.phone}`}>{t("callPerson")}</a>}
                  {!person.email && !person.phone && <span className="people-help">{t("contactNotShared")}</span>}
                </div>
              </article>
            ))}
          </div>
        )}
        {searchMode === "web" && hasSearched && !searching && (
          <>
            {publicNotice && <p className="people-help">{publicNotice}</p>}
            {publicProfiles.length === 0 ? <p className="people-empty">{t("noPublicProfilesFound")}</p> : (
              <div className="people-results">
                {publicProfiles.map((result) => (
                  <article className="people-result" key={result.url}>
                    <h4>{result.title || result.source}</h4>
                    <p>{result.snippet}</p>
                    <div className="people-contact-actions">
                      <a className="btn small primary" href={result.url} target="_blank" rel="noopener noreferrer">
                        {t("viewPublicProfile")}
                      </a>
                      <span className="people-help">{result.source}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
