"use strict";

// ---------------------------------------------------------------------------
// Suivi : choix « intéressante » / « ignorer », et candidatures gérées dans Airtable.
//
// Ce que le site écrit (fichier docs/data/suivi.chiffre.json du dépôt, chiffré
// avec le mot de passe du site, via l'API GitHub et un jeton saisi une fois
// par appareil) :
//  - tes choix ☆ / Ignorer ;
//  - les offres que tu ajoutes au suivi. La synchro Airtable (GitHub Actions,
//    collecte/airtable.py) en crée la fiche dans Airtable une minute plus tard.
//
// Ce que le site lit seulement : docs/data/airtable.chiffre.json, la copie de
// tes candidatures dans Airtable. La gestion (étapes, dates, notes) se fait
// dans Airtable ; la vue Suivi n'en est qu'un aperçu.
//
// Fusion des choix entre appareils : chaque élément porte sa date de
// modification (« maj ») ; la plus récente l'emporte, élément par élément.
// Une suppression est gardée comme une marque (« supprime »).
// ---------------------------------------------------------------------------

const Suivi = (() => {
  const CLE_LOCALE = "veille.suivi";
  const CLE_REPLIQUE = "veille.airtable";
  const CLE_JETON = "veille.jetonGithub";
  const CHEMIN = "docs/data/suivi.chiffre.json";
  const CHEMIN_AIRTABLE = "docs/data/airtable.chiffre.json";
  const DEPOT_PAR_DEFAUT = { proprietaire: "Robin-1506", nom: "Recherchetaff" };
  const ETAPES = ["À postuler", "Postulé", "Entretien", "Offre", "Refus"];
  const EN_ATTENTE = "Envoi vers Airtable…";

  let donnees = vide();
  let replique = null; // copie d'Airtable
  let minuterie = null;
  let synchroEnCours = null;

  function vide() {
    return { version: 1, offres: {}, candidatures: {} };
  }

  const maintenant = () => new Date().toISOString();
  const aujourdhui = () => new Date().toLocaleDateString("sv-SE"); // AAAA-MM-JJ, heure locale
  const aJeton = () => Boolean(local.lire(CLE_JETON));

  // --- Dépôt GitHub -----------------------------------------------------------

  function depot() {
    // Sur https://proprietaire.github.io/depot/, on déduit le dépôt de l'adresse.
    if (location.hostname.endsWith(".github.io")) {
      const nom = location.pathname.split("/")[1];
      if (nom) return { proprietaire: location.hostname.split(".")[0], nom };
    }
    return DEPOT_PAR_DEFAUT;
  }

  function urlApi(chemin) {
    const d = depot();
    return `https://api.github.com/repos/${d.proprietaire}/${d.nom}/contents/${chemin}`;
  }

  function entetes() {
    const h = { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
    const jeton = local.lire(CLE_JETON);
    if (jeton) h.Authorization = `Bearer ${jeton}`;
    return h;
  }

  async function lireDistant(chemin) {
    const r = await fetch(`${urlApi(chemin)}?ref=main&t=${Date.now()}`, { headers: entetes(), cache: "no-store" });
    if (r.status === 404) return { donnees: null, sha: null, sel: null };
    if (r.status === 401) throw new Error("Jeton GitHub refusé (expiré ou incorrect).");
    if (!r.ok) throw new Error(`Lecture du suivi impossible (GitHub, code ${r.status}).`);
    const fichier = await r.json();
    const enveloppe = JSON.parse(atob(fichier.content.replace(/\s/g, "")));
    return { donnees: await dechiffrer(enveloppe, etat.motDePasse), sha: fichier.sha, sel: enveloppe.sel };
  }

  async function ecrireDistant(contenu, sha, sel) {
    const enveloppe = await chiffrer(contenu, etat.motDePasse, sel);
    const corps = {
      message: "Mise à jour du suivi des candidatures",
      content: btoa(JSON.stringify(enveloppe)),
      branch: "main",
    };
    if (sha) corps.sha = sha;
    const r = await fetch(urlApi(CHEMIN), { method: "PUT", headers: entetes(), body: JSON.stringify(corps) });
    if (r.status === 409 || r.status === 422) {
      const err = new Error("Conflit de version");
      err.conflit = true;
      throw err;
    }
    if (r.status === 401 || r.status === 403 || r.status === 404) {
      throw new Error("Enregistrement refusé par GitHub : vérifie que le jeton a le droit « Contents : Read and write » sur ce dépôt.");
    }
    if (!r.ok) throw new Error(`Enregistrement impossible (GitHub, code ${r.status}).`);
  }

  // --- Fusion -----------------------------------------------------------------

  function fusionner(a, b) {
    const resultat = vide();
    for (const section of ["offres", "candidatures"]) {
      const cles = new Set([...Object.keys(a?.[section] || {}), ...Object.keys(b?.[section] || {})]);
      for (const cle of cles) {
        const x = a?.[section]?.[cle];
        const y = b?.[section]?.[cle];
        resultat[section][cle] = !x ? y : !y ? x : ((y.maj || "") > (x.maj || "") ? y : x);
      }
    }
    return resultat;
  }

  const identiques = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  // --- Synchronisation -----------------------------------------------------------

  function afficherEtatSynchro(texte, erreur = false) {
    const p = $("etat-synchro");
    p.textContent = texte;
    p.classList.toggle("erreur", erreur);
    $("bouton-synchro").textContent = erreur ? "Synchro ⚠️" : aJeton() ? "Synchro ✓" : "Synchro";
  }

  function sauverLocal() {
    local.ecrire(CLE_LOCALE, JSON.stringify(donnees));
  }

  // Copie d'Airtable : relue à chaque synchronisation (lecture seule).
  async function chargerReplique() {
    try {
      const lu = await lireDistant(CHEMIN_AIRTABLE);
      if (lu.donnees && !identiques(lu.donnees, replique)) {
        replique = lu.donnees;
        local.ecrire(CLE_REPLIQUE, JSON.stringify(replique));
        rafraichir();
      }
    } catch { /* on garde la copie déjà connue */ }
  }

  // Lit le fichier du dépôt, fusionne avec les données de l'appareil et,
  // si un jeton est présent et qu'il y a du nouveau, réécrit le fichier.
  async function synchroniser() {
    if (synchroEnCours) return synchroEnCours;
    synchroEnCours = (async () => {
      chargerReplique();
      for (let essai = 0; essai < 3; essai++) {
        try {
          afficherEtatSynchro("Synchronisation…");
          const distant = await lireDistant(CHEMIN);
          const fusion = fusionner(distant.donnees, donnees);
          const change = !identiques(fusion, donnees);
          donnees = fusion;
          sauverLocal();
          if (change) rafraichir();
          if (!aJeton()) {
            afficherEtatSynchro("Lecture seule sur cet appareil : ajoute ton jeton GitHub (bouton « Synchro ») pour enregistrer tes choix partout et envoyer tes ajouts à Airtable.");
            return;
          }
          if (!distant.donnees || !identiques(fusion, fusionner(distant.donnees, null))) {
            await ecrireDistant(fusion, distant.sha, distant.sel);
          }
          afficherEtatSynchro(`Synchronisé à ${new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}.`);
          return;
        } catch (e) {
          if (e.conflit && essai < 2) continue; // modifié ailleurs entre-temps : on recommence
          afficherEtatSynchro(`${e.message} Tes modifications restent enregistrées sur cet appareil.`, true);
          return;
        }
      }
    })();
    try { await synchroEnCours; } finally { synchroEnCours = null; }
  }

  function planifierSynchro() {
    clearTimeout(minuterie);
    afficherEtatSynchro(aJeton() ? "Modifications en attente d'envoi…" : "Enregistré sur cet appareil seulement.");
    minuterie = setTimeout(synchroniser, 1500);
  }

  function modifier(section, cle, valeur) {
    donnees[section][cle] = { ...valeur, maj: maintenant() };
    sauverLocal();
    planifierSynchro();
    rafraichir();
  }

  // --- Accès pour la liste des offres -------------------------------------------

  function statutOffre(id) {
    const s = donnees.offres[id];
    return s && !s.supprime ? s.statut : null;
  }

  function definirStatutOffre(id, statut) {
    modifier("offres", id, statut ? { statut } : { supprime: true });
  }

  // --- Candidatures ---------------------------------------------------------------

  const reference = (c) => c.offre_id || c.id;
  const candidatures = () => replique?.candidatures || [];
  const urlAirtable = (c) => replique?.base && replique?.table
    ? `https://airtable.com/${replique.base}/${replique.table}${c ? `/${c.airtable_id}` : ""}` : null;

  // Ajoutées dans le site, pas encore arrivées dans Airtable (même règle que collecte/airtable.py).
  function enAttente() {
    const transmises = replique?.transmises || {};
    return Object.values(donnees.candidatures)
      .filter((c) => !c.supprime && (c.maj || "") > (transmises[reference(c)] || ""));
  }

  // Pour la fiche d'une offre : { etape, url } si elle est dans le suivi, sinon null.
  function candidaturePourOffre(id) {
    const c = candidatures().find((x) => x.ref === id);
    if (c) return { etape: c.etape, url: urlAirtable(c) };
    return enAttente().some((x) => x.offre_id === id) ? { etape: EN_ATTENTE, enAttente: true } : null;
  }

  function creerDepuisOffre(o) {
    const existante = Object.values(donnees.candidatures).find((c) => c.offre_id === o.id);
    const id = existante?.id || crypto.randomUUID();
    const lien = (o.sources || []).map((s) => s.url).find(Boolean) || "";
    // Une nouvelle date « maj » suffit pour que la fiche soit (re)créée dans Airtable.
    modifier("candidatures", id, { id, offre_id: o.id, titre: o.titre, entreprise: o.entreprise || "", lien,
      colonne: "a_postuler" });
    if (statutOffre(o.id) !== "interessant") definirStatutOffre(o.id, "interessant");
    // La fiche arrive dans Airtable une à deux minutes plus tard : on relit la copie.
    for (const delai of [90, 180]) setTimeout(chargerReplique, delai * 1000);
  }

  // --- Vue Suivi (aperçu en lecture seule) ----------------------------------------

  const aRelancer = (c) => c.relance && c.relance <= aujourdhui() && ["postule", "entretien"].includes(c.colonne);

  function ligne(c) {
    const url = urlAirtable(c);
    const titre = el("span", { class: "offre-titre" }, c.titre);
    return el("li", { class: "ligne-suivi" + (aRelancer(c) ? " a-relancer" : "") },
      url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, titre) : titre,
      el("span", { class: "discret" }, [
        c.entreprise,
        c.date_candidature && `postulé le ${date(c.date_candidature)}`,
        c.relance && `${aRelancer(c) ? "⏰ relance" : "relance"} le ${date(c.relance)}`,
      ].filter(Boolean).join(" · ")),
    );
  }

  function groupe(nom, lignes, ouvert = true) {
    return el("details", { class: "groupe-suivi", open: ouvert },
      el("summary", {}, nom, el("span", { class: "discret" }, ` ${lignes.length}`)),
      el("ul", { class: "liste-suivi" }, lignes));
  }

  function etatAirtable() {
    if (!replique) {
      return el("p", { class: "discret" }, "Airtable n'est pas encore branché : tes ajouts au suivi y seront envoyés dès qu'il le sera.");
    }
    if (replique.erreur) return el("p", { class: "alerte" }, `⚠️ Dernière synchro Airtable en échec : ${replique.erreur}`);
    return el("p", { class: "discret" }, `Copie d'Airtable mise à jour le ${date(replique.maj, true)}. `
      + "Tes changements dans Airtable arrivent ici dans les heures qui suivent.");
  }

  function afficher() {
    const liste = candidatures();
    const url = urlAirtable();
    $("ouvrir-airtable").hidden = !url;
    if (url) $("ouvrir-airtable").href = url;
    const relances = liste.filter(aRelancer);
    $("a-relancer").replaceChildren(etatAirtable(), ...(relances.length ? [el("p", { class: "alerte" },
      `⏰ ${relances.length} candidature${relances.length > 1 ? "s" : ""} à relancer : `,
      relances.map((c) => c.entreprise || c.titre).join(", "))] : []));

    const attente = enAttente();
    // Étapes ajoutées par toi dans Airtable : avant « Refus », qui reste en dernier.
    const autres = [...new Set(liste.map((c) => c.etape).filter((e) => !ETAPES.includes(e)))];
    const etapes = [...ETAPES.slice(0, -1), ...autres, ETAPES.at(-1)];
    const groupes = etapes.map((etape) => {
      const lignes = liste.filter((c) => c.etape === etape)
        .sort((a, b) => (a.relance || "9999").localeCompare(b.relance || "9999") || a.titre.localeCompare(b.titre))
        .map(ligne);
      return lignes.length ? groupe(etape, lignes, etape !== "Refus") : null;
    }).filter(Boolean);
    if (attente.length) {
      groupes.unshift(groupe(`⏳ ${EN_ATTENTE}`, attente.map((c) => el("li", { class: "ligne-suivi" },
        el("span", { class: "offre-titre" }, c.titre || "(sans titre)"), el("span", { class: "discret" }, c.entreprise || "")))));
    }
    $("liste-suivi").replaceChildren(...(groupes.length ? groupes
      : [el("p", { class: "discret vide" }, "Aucune candidature. Ouvre une offre et clique sur « Ajouter au suivi ».")]));
  }

  function rafraichir() {
    const liste = candidatures();
    const enCours = liste.filter((c) => c.colonne !== "refus").length + enAttente().length;
    const relances = liste.filter(aRelancer).length;
    const pastille = $("nb-suivi");
    pastille.hidden = !enCours;
    pastille.textContent = relances ? `${enCours} · ⏰${relances}` : String(enCours);
    pastille.classList.toggle("alerte-pastille", relances > 0);
    if (!$("vue-suivi").hidden) afficher();
    if (typeof actualiserActionsFiche === "function" && $("fiche").open) actualiserActionsFiche();
  }

  // --- Réglages de synchronisation ----------------------------------------------------

  function ouvrirReglages() {
    $("jeton").value = local.lire(CLE_JETON) || "";
    $("synchro-message").textContent = aJeton()
      ? "Un jeton est enregistré sur cet appareil."
      : "Aucun jeton sur cet appareil : le suivi y est en lecture seule.";
    $("reglages-synchro").showModal();
  }

  async function enregistrerJeton(e) {
    e.preventDefault();
    const jeton = $("jeton").value.trim();
    if (!jeton) return;
    local.ecrire(CLE_JETON, jeton);
    $("synchro-message").textContent = "Test de la synchronisation…";
    await synchroniser();
    const erreur = $("etat-synchro").classList.contains("erreur");
    $("synchro-message").textContent = $("etat-synchro").textContent;
    if (!erreur) setTimeout(() => $("reglages-synchro").close(), 800);
  }

  function oublierJeton() {
    local.effacer(CLE_JETON);
    $("jeton").value = "";
    $("synchro-message").textContent = "Jeton effacé de cet appareil.";
    afficherEtatSynchro("Lecture seule sur cet appareil.");
  }

  // --- Démarrage ----------------------------------------------------------------

  let branche = false;
  function brancherSuivi() {
    if (branche) return;
    branche = true;
    $("bouton-synchro").addEventListener("click", ouvrirReglages);
    $("form-synchro").addEventListener("submit", enregistrerJeton);
    $("oublier-jeton").addEventListener("click", oublierJeton);
    // Revenir sur l'onglet : on récupère les modifications faites ailleurs.
    document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && synchroniser());
  }

  function demarrer() {
    brancherSuivi();
    try { donnees = fusionner(JSON.parse(local.lire(CLE_LOCALE) || "null"), null); } catch { donnees = vide(); }
    try { replique = JSON.parse(local.lire(CLE_REPLIQUE) || "null"); } catch { replique = null; }
    rafraichir();
    synchroniser();
  }

  return {
    demarrer, afficher, synchroniser, statutOffre, definirStatutOffre, candidaturePourOffre, creerDepuisOffre, aJeton,
    _fusionner: fusionner, // pour les tests
  };
})();
