"use strict";

// ---------------------------------------------------------------------------
// Suivi des candidatures et choix « intéressante » / « ignorer ».
//
// Stockage :
//  - une copie sur l'appareil (stockage du navigateur), pour un affichage immédiat ;
//  - le fichier docs/data/suivi.chiffre.json du dépôt GitHub, chiffré avec le
//    mot de passe du site. Il est lu et écrit via l'API GitHub, avec un jeton
//    personnel saisi une fois par appareil. C'est ce qui synchronise
//    téléphone et ordinateur, et ce que lit le récap du matin.
//
// Fusion : chaque élément porte sa date de modification (« maj ») ; en cas de
// modifications sur deux appareils, la plus récente l'emporte, élément par
// élément. Une suppression est gardée comme une marque (« supprime »).
// ---------------------------------------------------------------------------

const Suivi = (() => {
  const CLE_LOCALE = "veille.suivi";
  const CLE_JETON = "veille.jetonGithub";
  const CHEMIN = "docs/data/suivi.chiffre.json";
  const DEPOT_PAR_DEFAUT = { proprietaire: "Robin-1506", nom: "Recherchetaff" };
  const DELAI_RELANCE_JOURS = 7;
  const COLONNES = [
    ["a_postuler", "À postuler"],
    ["postule", "Postulé"],
    ["entretien", "Entretien"],
    ["offre", "Offre"],
    ["refus", "Refus"],
  ];

  let donnees = vide();
  let ouverte = null; // candidature en cours d'édition
  let minuterie = null;
  let synchroEnCours = null;

  function vide() {
    return { version: 1, offres: {}, candidatures: {} };
  }

  const maintenant = () => new Date().toISOString();
  const aujourdhui = () => new Date().toLocaleDateString("sv-SE"); // AAAA-MM-JJ, heure locale
  function plusJours(jour, n) {
    const d = new Date(`${jour}T12:00:00`);
    d.setDate(d.getDate() + n);
    return d.toLocaleDateString("sv-SE");
  }

  // --- Dépôt GitHub -----------------------------------------------------------

  function depot() {
    // Sur https://proprietaire.github.io/depot/, on déduit le dépôt de l'adresse.
    if (location.hostname.endsWith(".github.io")) {
      const nom = location.pathname.split("/")[1];
      if (nom) return { proprietaire: location.hostname.split(".")[0], nom };
    }
    return DEPOT_PAR_DEFAUT;
  }

  function urlApi() {
    const d = depot();
    return `https://api.github.com/repos/${d.proprietaire}/${d.nom}/contents/${CHEMIN}`;
  }

  function entetes() {
    const h = { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
    const jeton = local.lire(CLE_JETON);
    if (jeton) h.Authorization = `Bearer ${jeton}`;
    return h;
  }

  async function lireDistant() {
    const r = await fetch(`${urlApi()}?ref=main&t=${Date.now()}`, { headers: entetes(), cache: "no-store" });
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
    const r = await fetch(urlApi(), { method: "PUT", headers: entetes(), body: JSON.stringify(corps) });
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
    $("bouton-synchro").textContent = erreur ? "Synchro ⚠️" : local.lire(CLE_JETON) ? "Synchro ✓" : "Synchro";
  }

  function sauverLocal() {
    local.ecrire(CLE_LOCALE, JSON.stringify(donnees));
  }

  // Lit le fichier du dépôt, fusionne avec les données de l'appareil et,
  // si un jeton est présent et qu'il y a du nouveau, réécrit le fichier.
  async function synchroniser() {
    if (synchroEnCours) return synchroEnCours;
    synchroEnCours = (async () => {
      const avecJeton = Boolean(local.lire(CLE_JETON));
      for (let essai = 0; essai < 3; essai++) {
        try {
          afficherEtatSynchro("Synchronisation…");
          const distant = await lireDistant();
          const fusion = fusionner(distant.donnees, donnees);
          const change = !identiques(fusion, donnees);
          donnees = fusion;
          sauverLocal();
          if (change) rafraichir();
          if (!avecJeton) {
            afficherEtatSynchro("Lecture seule sur cet appareil : ajoute ton jeton GitHub (bouton « Synchro ») pour enregistrer tes modifications partout.");
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
    afficherEtatSynchro(local.lire(CLE_JETON) ? "Modifications en attente d'envoi…" : "Enregistré sur cet appareil seulement.");
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

  const actives = () => Object.values(donnees.candidatures).filter((c) => !c.supprime);
  const candidaturePourOffre = (id) => actives().find((c) => c.offre_id === id) || null;
  const nomColonne = (id) => (COLONNES.find(([c]) => c === id) || [, id])[1];

  function creerDepuisOffre(o) {
    const id = crypto.randomUUID();
    const lien = (o.sources || []).map((s) => s.url).find(Boolean) || "";
    modifier("candidatures", id, {
      id, offre_id: o.id, titre: o.titre, entreprise: o.entreprise || "", lien,
      colonne: "a_postuler", date_candidature: "", relance: "", contact: "", notes: "",
    });
    if (statutOffre(o.id) !== "interessant") definirStatutOffre(o.id, "interessant");
    return donnees.candidatures[id];
  }

  // --- Tableau (kanban) ---------------------------------------------------------

  const aRelancer = (c) => c.relance && c.relance <= aujourdhui() && ["postule", "entretien"].includes(c.colonne);

  function deplacer(c, sens) {
    const i = COLONNES.findIndex(([id]) => id === c.colonne);
    const colonne = COLONNES[Math.min(COLONNES.length - 1, Math.max(0, i + sens))][0];
    modifier("candidatures", c.id, avecRegles({ ...c, colonne }, c.colonne));
  }

  // Règles automatiques quand une candidature change d'étape.
  function avecRegles(c, ancienneColonne) {
    if (c.colonne === "postule" && ancienneColonne !== "postule") {
      c.date_candidature = c.date_candidature || aujourdhui();
      c.relance = c.relance || plusJours(c.date_candidature, DELAI_RELANCE_JOURS);
    }
    if (["offre", "refus"].includes(c.colonne)) c.relance = "";
    return c;
  }

  function carte(c) {
    const i = COLONNES.findIndex(([id]) => id === c.colonne);
    return el("li", { class: "carte candidature" + (aRelancer(c) ? " a-relancer" : "") },
      el("button", { type: "button", class: "candidature-ouvrir", onclick: () => ouvrirCandidature(c.id) },
        el("span", { class: "offre-titre" }, c.titre || "(sans titre)"),
        c.entreprise && el("span", { class: "offre-ligne" }, c.entreprise),
        c.date_candidature && el("span", { class: "offre-ligne" }, `Postulé le ${date(c.date_candidature)}`),
        c.relance && el("span", { class: "offre-ligne" + (aRelancer(c) ? " relance-due" : "") },
          `${aRelancer(c) ? "⏰ À relancer" : "Relance"} le ${date(c.relance)}`),
      ),
      el("div", { class: "candidature-fleches" },
        el("button", { type: "button", class: "bouton-discret", disabled: i === 0, "aria-label": "Étape précédente",
          onclick: () => deplacer(c, -1) }, "←"),
        el("button", { type: "button", class: "bouton-discret", disabled: i === COLONNES.length - 1,
          "aria-label": "Étape suivante", onclick: () => deplacer(c, 1) }, "→"),
      ),
    );
  }

  function afficher() {
    const liste = actives();
    const relances = liste.filter(aRelancer);
    $("a-relancer").replaceChildren(...(relances.length ? [el("p", { class: "alerte" },
      `⏰ ${relances.length} candidature${relances.length > 1 ? "s" : ""} à relancer : `,
      relances.map((c) => c.entreprise || c.titre).join(", "))] : []));
    $("kanban").replaceChildren(...COLONNES.map(([id, nom]) => {
      const cartes = liste.filter((c) => c.colonne === id)
        .sort((a, b) => (a.relance || "9999").localeCompare(b.relance || "9999") || (b.maj || "").localeCompare(a.maj || ""));
      return el("section", { class: "colonne" },
        el("h3", {}, nom, el("span", { class: "discret" }, ` ${cartes.length}`)),
        cartes.length ? el("ul", { class: "liste" }, cartes.map(carte)) : el("p", { class: "discret vide" }, "Aucune"),
      );
    }));
  }

  function rafraichir() {
    const liste = actives();
    const enCours = liste.filter((c) => c.colonne !== "refus").length;
    const relances = liste.filter(aRelancer).length;
    const pastille = $("nb-suivi");
    pastille.hidden = !enCours;
    pastille.textContent = relances ? `${enCours} · ⏰${relances}` : String(enCours);
    pastille.classList.toggle("alerte-pastille", relances > 0);
    if (!$("vue-suivi").hidden) afficher();
    if (typeof actualiserActionsFiche === "function" && $("fiche").open) actualiserActionsFiche();
  }

  // --- Fiche de candidature -----------------------------------------------------

  function champs() {
    return $("form-candidature").elements;
  }

  function ouvrirCandidature(id) {
    ouverte = id ? donnees.candidatures[id] : null;
    const c = ouverte || { colonne: "a_postuler" };
    const f = champs();
    for (const nom of ["titre", "entreprise", "lien", "date_candidature", "relance", "contact", "notes"]) {
      f[nom].value = c[nom] || "";
    }
    f.colonne.replaceChildren(...COLONNES.map(([v, nom]) => el("option", { value: v, selected: v === c.colonne }, nom)));
    $("candidature-titre").textContent = ouverte ? "Candidature" : "Nouvelle candidature";
    $("b-supprimer-candidature").hidden = !ouverte;
    $("b-relance-faite").hidden = !ouverte;
    $("candidature").showModal();
  }

  function enregistrerFiche() {
    const f = champs();
    const id = ouverte ? ouverte.id : crypto.randomUUID();
    const avant = ouverte || {};
    const c = { ...avant, id };
    for (const nom of ["titre", "entreprise", "lien", "colonne", "date_candidature", "relance", "contact", "notes"]) {
      c[nom] = f[nom].value.trim();
    }
    modifier("candidatures", id, avecRegles(c, avant.colonne));
  }

  function relanceFaite() {
    const c = { ...ouverte };
    const jour = aujourdhui();
    c.relance = plusJours(jour, DELAI_RELANCE_JOURS);
    c.notes = `${c.notes ? c.notes + "\n" : ""}${date(jour)} : relance effectuée.`;
    modifier("candidatures", c.id, c);
    $("candidature").close();
  }

  function supprimer() {
    if (!confirm("Supprimer cette candidature ?")) return;
    modifier("candidatures", ouverte.id, { id: ouverte.id, supprime: true });
    $("candidature").close();
  }

  // --- Export / import (sauvegarde) ------------------------------------------------

  function exporter() {
    const blob = new Blob([JSON.stringify(donnees, null, 2)], { type: "application/json" });
    const a = el("a", { href: URL.createObjectURL(blob), download: `suivi-candidatures-${aujourdhui()}.json` });
    document.body.append(a);
    a.click();
    a.remove();
  }

  async function importer(fichier) {
    try {
      const contenu = JSON.parse(await fichier.text());
      if (!contenu.candidatures || !contenu.offres) throw new Error();
      donnees = fusionner(donnees, contenu);
      sauverLocal();
      planifierSynchro();
      rafraichir();
      afficherEtatSynchro("Sauvegarde importée.");
    } catch {
      afficherEtatSynchro("Ce fichier n'est pas une sauvegarde du suivi.", true);
    }
  }

  // --- Réglages de synchronisation ----------------------------------------------------

  function ouvrirReglages() {
    $("jeton").value = local.lire(CLE_JETON) || "";
    $("synchro-message").textContent = local.lire(CLE_JETON)
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
    $("nouvelle-candidature").addEventListener("click", () => ouvrirCandidature(null));
    $("form-candidature").addEventListener("submit", enregistrerFiche);
    $("b-relance-faite").addEventListener("click", relanceFaite);
    $("b-supprimer-candidature").addEventListener("click", supprimer);
    $("exporter-suivi").addEventListener("click", exporter);
    $("importer-suivi").addEventListener("change", (e) => e.target.files[0] && importer(e.target.files[0]));
    $("bouton-synchro").addEventListener("click", ouvrirReglages);
    $("form-synchro").addEventListener("submit", enregistrerJeton);
    $("oublier-jeton").addEventListener("click", oublierJeton);
    // Revenir sur l'onglet : on récupère les modifications faites sur l'autre appareil.
    document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && synchroniser());
  }

  function demarrer() {
    brancherSuivi();
    try { donnees = fusionner(JSON.parse(local.lire(CLE_LOCALE) || "null"), null); } catch { donnees = vide(); }
    rafraichir();
    synchroniser();
  }

  return {
    demarrer, afficher, synchroniser, statutOffre, definirStatutOffre, candidaturePourOffre, creerDepuisOffre,
    ouvrirCandidature, nomColonne,
    _fusionner: fusionner, // pour les tests
  };
})();
