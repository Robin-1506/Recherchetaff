"use strict";

// ---------------------------------------------------------------------------
// Suivi : choix « intéressante » / « ignorer », et candidatures gérées dans Airtable.
//
// Ce que le site écrit (fichier docs/data/suivi.chiffre.json du dépôt, chiffré
// avec le mot de passe du site, via l'API GitHub et un jeton saisi une fois
// par appareil) :
//  - tes choix ☆ / Ignorer ;
//  - les offres que tu ajoutes au suivi ;
//  - tes demandes faites dans le kanban : changement d'étape (glisser une carte)
//    et commentaires. La synchro Airtable (GitHub Actions, collecte/airtable.py)
//    les applique dans Airtable une à deux minutes plus tard.
//
// Ce que le site lit : docs/data/airtable.chiffre.json, la copie de tes
// candidatures dans Airtable (notes comprises). En attendant qu'elles arrivent
// dans Airtable, tes demandes sont affichées par-dessus cette copie.
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
  const CODES = { "À postuler": "a_postuler", "Postulé": "postule", "Entretien": "entretien", "Offre": "offre", "Refus": "refus" };
  const COULEURS = { "À postuler": "gris", "Postulé": "bleu", "Entretien": "violet", "Offre": "vert", "Refus": "rouge" };
  const EN_ATTENTE = "Envoi vers Airtable…";

  let donnees = vide();
  let replique = null; // copie d'Airtable
  let minuterie = null;
  let synchroEnCours = null;

  function vide() {
    return { version: 1, offres: {}, candidatures: {}, modifications: {} };
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
    for (const section of ["offres", "candidatures", "modifications"]) {
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
  const urlAirtable = (c) => replique?.base && replique?.table
    ? `https://airtable.com/${replique.base}/${replique.table}${c ? `/${c.airtable_id}` : ""}` : null;

  // Ajoutées dans le site, pas encore arrivées dans Airtable (même règle que collecte/airtable.py).
  function enAttente() {
    const transmises = replique?.transmises || {};
    return Object.values(donnees.candidatures)
      .filter((c) => !c.supprime && (c.maj || "") > (transmises[reference(c)] || ""));
  }

  // Étapes et commentaires faits dans le site, pas encore appliqués dans Airtable.
  function demandesEnAttente() {
    const appliquees = replique?.appliquees || {};
    return Object.entries(donnees.modifications || {})
      .filter(([id, m]) => !m.supprime && !(id in appliquees))
      .map(([id, m]) => ({ ...m, id }))
      .sort((a, b) => (a.date || "").localeCompare(b.date || ""));
  }

  // Cartes du kanban : la copie d'Airtable, avec tes changements pas encore arrivés
  // là-bas déjà appliqués, plus les offres en route vers Airtable.
  function cartes() {
    const demandes = demandesEnAttente();
    const liste = (replique?.candidatures || []).map((c) => {
      const miennes = demandes.filter((m) => m.airtable_id === c.airtable_id);
      const etape = miennes.filter((m) => m.type === "etape").at(-1)?.etape || c.etape;
      return { ...c, etape, colonne: CODES[etape] ?? null, deplacee: etape !== c.etape,
        commentaires: miennes.filter((m) => m.type === "commentaire") };
    });
    const enRoute = enAttente().map((c) => ({ cle: `attente-${c.id}`, ref: reference(c), titre: c.titre || "(sans titre)",
      entreprise: c.entreprise || "", etape: ETAPES[0], colonne: "a_postuler", enRoute: true, commentaires: [] }));
    return [...enRoute, ...liste];
  }
  const cle = (c) => c.cle || c.airtable_id;

  // Pour la fiche d'une offre : { etape, url } si elle est dans le suivi, sinon null.
  function candidaturePourOffre(id) {
    const c = cartes().find((x) => x.ref === id);
    if (!c) return null;
    return c.enRoute ? { etape: EN_ATTENTE, enAttente: true } : { etape: c.etape, url: urlAirtable(c) };
  }

  function creerDepuisOffre(o) {
    const existante = Object.values(donnees.candidatures).find((c) => c.offre_id === o.id);
    const id = existante?.id || crypto.randomUUID();
    const lien = (o.sources || []).map((s) => s.url).find(Boolean) || "";
    // Une nouvelle date « maj » suffit pour que la fiche soit (re)créée dans Airtable.
    modifier("candidatures", id, { id, offre_id: o.id, titre: o.titre, entreprise: o.entreprise || "", lien,
      colonne: "a_postuler" });
    if (statutOffre(o.id) !== "interessant") definirStatutOffre(o.id, "interessant");
    relireBientot();
  }

  // Les changements arrivent dans Airtable une à deux minutes plus tard : on relit la copie.
  function relireBientot() {
    for (const delai of [90, 180]) setTimeout(chargerReplique, delai * 1000);
  }

  function demander(demande) {
    modifier("modifications", crypto.randomUUID(), { ...demande, date: maintenant() });
    relireBientot();
  }

  function changerEtape(c, etape) {
    if (!c || c.enRoute || etape === c.etape) return;
    demander({ type: "etape", airtable_id: c.airtable_id, etape });
  }

  // --- Kanban -----------------------------------------------------------------------

  const aRelancer = (c) => c.relance && c.relance <= aujourdhui() && ["postule", "entretien"].includes(c.colonne);
  const couleur = (etape) => COULEURS[etape] || "jaune";
  const jourCourt = (jour) => new Date(`${jour}T12:00:00`).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  const nbNotes = (c) => (c.notes || "").split("\n").filter((l) => l.trim()).length + c.commentaires.length;
  let glissee = null; // carte en cours de glisser-déposer

  function etapesAffichees(liste) {
    // Étapes ajoutées par toi dans Airtable : avant « Refus », qui reste en dernier.
    const autres = [...new Set(liste.map((c) => c.etape).filter((e) => e && !ETAPES.includes(e)))];
    return [...ETAPES.slice(0, -1), ...autres, ETAPES.at(-1)];
  }

  function pastilleEtape(etape) {
    return el("span", { class: `kb-etape c-${couleur(etape)}` }, etape);
  }

  function score(c) {
    const o = etat.offres.find((x) => x.id === c.ref);
    return typeof o?.score === "number" ? o.score : null;
  }

  function carteKanban(c) {
    const s = score(c);
    const chips = [
      c.enRoute && el("span", { class: "kb-chip" }, "⏳ En route vers Airtable"),
      c.deplacee && el("span", { class: "kb-chip" }, "⏳ Envoi…"),
      c.relance && el("span", { class: "kb-chip" + (aRelancer(c) ? " kb-chip-alerte" : "") },
        `${aRelancer(c) ? "⏰" : "↻"} ${jourCourt(c.relance)}`),
      c.date_candidature && el("span", { class: "kb-chip", title: "Date de candidature" }, `✉ ${jourCourt(c.date_candidature)}`),
      nbNotes(c) > 0 && el("span", { class: "kb-chip", title: "Notes" }, `💬 ${nbNotes(c)}`),
      s !== null && el("span", { class: `kb-chip kb-score ${classeScore(s)}`, title: "Score de pertinence" }, String(s)),
    ].filter(Boolean);
    const initiale = (c.entreprise || c.titre || "?").trim().charAt(0).toUpperCase();
    return el("article", {
      class: "kb-carte" + (c.enRoute ? " kb-en-route" : "") + (aRelancer(c) ? " kb-relance" : ""),
      tabindex: "0", draggable: c.enRoute ? null : "true", "data-cle": cle(c),
      onclick: () => ouvrirCarte(cle(c)),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ouvrirCarte(cle(c)); } },
      ondragstart: (e) => {
        glissee = c;
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", cle(c));
        e.currentTarget.classList.add("kb-glissee");
      },
      ondragend: (e) => { e.currentTarget.classList.remove("kb-glissee"); glissee = null; },
    },
      el("div", { class: "kb-titre" }, c.titre),
      c.entreprise && el("div", { class: "kb-entreprise" }, el("span", { class: "kb-avatar", "aria-hidden": "true" }, initiale), c.entreprise),
      chips.length > 0 && el("div", { class: "kb-chips" }, chips),
    );
  }

  function colonneKanban(etape, liste) {
    const cartesEtape = liste.filter((c) => c.etape === etape)
      .sort((a, b) => (a.relance || "9999").localeCompare(b.relance || "9999") || a.titre.localeCompare(b.titre));
    const zone = el("div", { class: "kb-cartes" }, cartesEtape.map(carteKanban),
      cartesEtape.length ? null : el("p", { class: "kb-vide" }, "Aucune carte"));
    const colonne = el("section", { class: `kb-colonne c-${couleur(etape)}`, "aria-label": etape },
      el("header", { class: "kb-entete" }, pastilleEtape(etape), el("span", { class: "kb-nb" }, String(cartesEtape.length))),
      zone);
    colonne.addEventListener("dragover", (e) => {
      if (!glissee) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      colonne.classList.add("kb-survol");
    });
    colonne.addEventListener("dragleave", (e) => { if (!colonne.contains(e.relatedTarget)) colonne.classList.remove("kb-survol"); });
    colonne.addEventListener("drop", (e) => {
      e.preventDefault();
      colonne.classList.remove("kb-survol");
      changerEtape(glissee, etape);
    });
    return colonne;
  }

  function etatAirtable() {
    if (!replique) {
      return el("p", { class: "discret" }, "Airtable n'est pas encore branché : tes ajouts au suivi y seront envoyés dès qu'il le sera.");
    }
    if (replique.erreur) return el("p", { class: "alerte" }, `⚠️ Dernière synchro Airtable en échec : ${replique.erreur}`);
    return el("p", { class: "discret" }, `Copie d'Airtable du ${date(replique.maj, true)}. Glisse une carte pour changer `
      + "d'étape ; tes changements arrivent dans Airtable une à deux minutes plus tard.");
  }

  function afficher() {
    const liste = cartes();
    const url = urlAirtable();
    $("ouvrir-airtable").hidden = !url;
    if (url) $("ouvrir-airtable").href = url;
    const relances = liste.filter(aRelancer);
    $("a-relancer").replaceChildren(etatAirtable(), ...(relances.length ? [el("p", { class: "alerte" },
      `⏰ ${relances.length} candidature${relances.length > 1 ? "s" : ""} à relancer : `,
      relances.map((c) => c.entreprise || c.titre).join(", "))] : []));
    $("kanban").replaceChildren(...etapesAffichees(liste).map((etape) => colonneKanban(etape, liste)));
    if ($("carte-suivi").open) remplirCarte();
  }

  // --- Fiche d'une carte ----------------------------------------------------------------

  let carteOuverte = null; // clé de la carte affichée

  function propriete(nom, valeur) {
    return valeur ? [el("dt", {}, nom), el("dd", {}, valeur)] : [];
  }

  function remplirCarte() {
    const c = cartes().find((x) => cle(x) === carteOuverte);
    if (!c) { $("carte-suivi").close(); return; }
    const choix = etapesAffichees(cartes());
    const selecteur = el("select", { class: `kb-etape-choix c-${couleur(c.etape)}`, "aria-label": "Étape", disabled: c.enRoute || null,
      onchange: (e) => changerEtape(c, e.target.value) },
      choix.map((e) => el("option", { value: e, selected: e === c.etape || null }, e)));
    const offre = etat.offres.find((o) => o.id === c.ref);
    const urlFiche = c.enRoute ? null : urlAirtable(c);
    const notes = (c.notes || "").split("\n").filter((l) => l.trim());

    $("carte-suivi-titre").textContent = c.titre;
    $("carte-suivi-entreprise").textContent = [c.entreprise, offre?.lieu].filter(Boolean).join(" · ");
    $("carte-suivi-proprietes").replaceChildren(
      el("dt", {}, "Étape"), el("dd", {}, selecteur, c.deplacee ? el("span", { class: "discret" }, " ⏳ envoi vers Airtable…") : null),
      ...propriete("Candidature", c.date_candidature && date(c.date_candidature)),
      ...propriete("Relance", c.relance && el("span", { class: aRelancer(c) ? "erreur" : "" },
        `${aRelancer(c) ? "⏰ " : ""}${date(c.relance)}`)),
      ...propriete("Contact", c.contact),
      ...propriete("Offre", urlSure(c.lien) && el("a", { href: urlSure(c.lien), target: "_blank", rel: "noopener noreferrer" }, "Voir l'annonce ↗")),
    );
    $("carte-suivi-actions").replaceChildren(
      urlFiche && el("a", { class: "bouton-discret", href: urlFiche, target: "_blank", rel: "noopener noreferrer" }, "Ouvrir dans Airtable ↗"),
      offre && el("button", { type: "button", class: "bouton-discret", onclick: () => { $("carte-suivi").close(); ouvrirFiche(offre); } },
        "Fiche de l'offre"),
    );
    $("carte-suivi-notes").replaceChildren(
      ...(notes.length || c.commentaires.length ? [] : [el("li", { class: "discret" }, "Aucune note pour l'instant.")]),
      ...notes.map((l) => el("li", {}, l)),
      ...c.commentaires.map((m) => el("li", { class: "kb-note-attente" },
        `${new Date(m.date).toLocaleDateString("fr-FR")} : ${m.texte}`, el("span", { class: "discret" }, " ⏳ envoi vers Airtable…"))),
    );
    $("form-commentaire").hidden = Boolean(c.enRoute);
    $("carte-suivi-attente").hidden = !c.enRoute;
  }

  function ouvrirCarte(cleCarte) {
    carteOuverte = cleCarte;
    $("commentaire").value = "";
    remplirCarte();
    if (!$("carte-suivi").open) $("carte-suivi").showModal();
  }

  function ajouterCommentaire(e) {
    e.preventDefault();
    const texte = $("commentaire").value.trim();
    const c = cartes().find((x) => cle(x) === carteOuverte);
    if (!texte || !c || c.enRoute) return;
    demander({ type: "commentaire", airtable_id: c.airtable_id, texte });
    $("commentaire").value = "";
  }

  function rafraichir() {
    const liste = cartes();
    const enCours = liste.filter((c) => c.colonne !== "refus").length;
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
    $("form-commentaire").addEventListener("submit", ajouterCommentaire);
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
