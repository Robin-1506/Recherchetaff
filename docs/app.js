"use strict";

// ---------------------------------------------------------------------------
// Veille emploi Nantes : affichage des offres collectées chaque jour.
// Les données sont chiffrées (voir collecte/chiffrement.py) et déchiffrées
// ici, dans le navigateur, avec le mot de passe saisi.
// ---------------------------------------------------------------------------

const FICHIER_DONNEES = "data/offres.chiffre.json";
const FICHIER_STATUT = "data/statut.json";
const CLE_MDP = "veille.motDePasse";
const CLE_VISITE = "veille.derniereVisite";
const CLE_REFERENCE = "veille.referenceNouveautes";

const etat = { offres: [], reference: null };

// --- Petits utilitaires ----------------------------------------------------

const $ = (id) => document.getElementById(id);

function stockage(type) {
  // Le stockage du navigateur peut être indisponible (navigation privée…).
  return {
    lire(cle) { try { return window[type].getItem(cle); } catch { return null; } },
    ecrire(cle, val) { try { window[type].setItem(cle, val); } catch { /* ignoré */ } },
    effacer(cle) { try { window[type].removeItem(cle); } catch { /* ignoré */ } },
  };
}
const local = stockage("localStorage");
const session = stockage("sessionStorage");

function el(balise, attributs = {}, ...enfants) {
  const n = document.createElement(balise);
  for (const [k, v] of Object.entries(attributs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const e of enfants.flat()) {
    if (e === null || e === undefined || e === false) continue;
    n.append(e instanceof Node ? e : document.createTextNode(String(e)));
  }
  return n;
}

function urlSure(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch { return null; }
}

const formatDate = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric" });
const formatDateHeure = new Intl.DateTimeFormat("fr-FR", { dateStyle: "long", timeStyle: "short" });
function date(iso, avecHeure = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : (avecHeure ? formatDateHeure : formatDate).format(d);
}

function sansAccents(t) {
  return (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// --- Déchiffrement -----------------------------------------------------------

async function dechiffrer(enveloppe, motDePasse) {
  const octets = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const base = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(motDePasse), "PBKDF2", false, ["deriveKey"]);
  const cle = await crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: octets(enveloppe.sel), iterations: enveloppe.iterations, hash: "SHA-256" },
    base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const clair = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: octets(enveloppe.iv) }, cle, octets(enveloppe.donnees));
  const flux = new Blob([clair]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(flux).text());
}

async function chargerJSON(chemin) {
  const r = await fetch(chemin, { cache: "no-cache" });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`Chargement impossible (${r.status})`);
  return r.json();
}

// --- Démarrage ---------------------------------------------------------------

async function demarrer(motDePasse, memoriser) {
  const enveloppe = await chargerJSON(FICHIER_DONNEES);
  let donnees = { offres: [] };
  if (enveloppe) {
    try {
      donnees = await dechiffrer(enveloppe, motDePasse);
    } catch {
      throw new Error("Mot de passe incorrect.");
    }
  }
  if (memoriser) local.ecrire(CLE_MDP, motDePasse);
  session.ecrire(CLE_MDP, motDePasse);

  // « Nouvelles depuis la dernière visite » : la référence est figée pour
  // toute la session (un rechargement de page ne fait pas disparaître les badges).
  let reference = session.lire(CLE_REFERENCE);
  if (reference === null) {
    reference = local.lire(CLE_VISITE) || "";
    session.ecrire(CLE_REFERENCE, reference);
    local.ecrire(CLE_VISITE, new Date().toISOString());
  }
  etat.reference = reference ? new Date(reference) : null;
  etat.offres = donnees.offres || [];

  $("ecran-verrou").hidden = true;
  $("app").hidden = false;
  afficherStatut(await chargerJSON(FICHIER_STATUT).catch(() => null), !enveloppe);
  remplirFiltres();
  afficherListe();
}

function afficherStatut(statut, aucuneDonnee) {
  const info = $("info-collecte");
  const alertes = $("alertes-sources");
  alertes.replaceChildren();
  if (!statut || !statut.derniere_collecte) {
    info.textContent = "Aucune collecte n'a encore eu lieu.";
    return;
  }
  info.textContent = `Dernière collecte : ${date(statut.derniere_collecte, true)}`;
  for (const s of Object.values(statut.sources || {})) {
    if (s.non_configuree) {
      alertes.append(el("p", { class: "alerte info" }, `ℹ️ ${s.nom} n'est pas encore configurée.`));
    } else if (!s.ok) {
      alertes.append(el("p", { class: "alerte" }, `⚠️ ${s.nom} n'a pas pu être consultée lors de la dernière collecte : ${s.message}`));
    } else if (s.message) {
      alertes.append(el("p", { class: "alerte" }, `ℹ️ ${s.nom} : collecte partielle (${s.message})`));
    }
  }
  if (aucuneDonnee) alertes.append(el("p", { class: "alerte" }, "Aucune offre enregistrée pour l'instant."));
}

// --- Filtres et liste --------------------------------------------------------

function estNouvelle(o) {
  return !etat.reference || new Date(o.premiere_vue) > etat.reference;
}

function remplirFiltres() {
  const ajouter = (select, valeurs) => {
    for (const v of [...new Set(valeurs)].filter(Boolean).sort()) select.append(el("option", { value: v }, v));
  };
  ajouter($("f-contrat"), etat.offres.map((o) => o.contrat));
  ajouter($("f-source"), etat.offres.flatMap((o) => (o.sources || []).map((s) => s.nom)));
}

function offresFiltrees() {
  const texte = sansAccents($("f-texte").value.trim());
  const contrat = $("f-contrat").value;
  const tele = $("f-teletravail").value;
  const source = $("f-source").value;
  const nouvelles = $("f-nouvelles").checked;
  const expirees = $("f-expirees").checked;
  return etat.offres
    .filter((o) => expirees || !o.expiree)
    .filter((o) => !contrat || o.contrat === contrat)
    .filter((o) => !tele || (tele === "aucun" ? !o.teletravail : o.teletravail === tele))
    .filter((o) => !source || (o.sources || []).some((s) => s.nom === source))
    .filter((o) => !nouvelles || estNouvelle(o))
    .filter((o) => !texte || sansAccents([o.titre, o.entreprise, o.lieu, o.description].join(" ")).includes(texte))
    .sort((a, b) => (b.date_maj || "").localeCompare(a.date_maj || ""));
}

function badges(o) {
  return [
    estNouvelle(o) && !o.expiree && el("span", { class: "badge nouveau" }, "Nouvelle"),
    o.expiree && el("span", { class: "badge attention" }, "Expirée"),
    o.contrat && el("span", { class: "badge" + (["Freelance", "Intérim"].includes(o.contrat) ? " attention" : "") }, o.contrat),
    o.teletravail === "complet" && el("span", { class: "badge" }, o.hors_zone ? "Full remote (hors Nantes)" : "Télétravail complet"),
    o.teletravail === "partiel" && el("span", { class: "badge" }, "Télétravail partiel"),
    ...(o.sources || []).map((s) => el("span", { class: "badge" }, s.nom)),
  ].filter(Boolean);
}

function afficherListe() {
  const offres = offresFiltrees();
  const nb = offres.length;
  $("compteur").textContent = `${nb} offre${nb > 1 ? "s" : ""}`;
  $("liste").replaceChildren(...offres.map((o) => el("li", {},
    el("button", { type: "button", class: "carte offre" + (o.expiree ? " expiree" : ""), onclick: () => ouvrirFiche(o) },
      el("span", { class: "offre-titre" }, o.titre),
      el("span", { class: "offre-ligne" }, [o.entreprise || "Entreprise non précisée", o.lieu].filter(Boolean).join(" · ")),
      el("span", { class: "offre-ligne" }, `Mise à jour le ${date(o.date_maj)}`),
      el("span", { class: "badges" }, badges(o)),
    ),
  )));
}

// --- Fiche détaillée ---------------------------------------------------------

function ouvrirFiche(o) {
  $("fiche-titre").textContent = o.titre;
  $("fiche-sous-titre").textContent = [o.entreprise || "Entreprise non précisée", o.lieu].filter(Boolean).join(" · ");
  $("fiche-badges").replaceChildren(...badges(o));

  const infos = [
    ["Contrat", o.contrat_libelle || o.contrat],
    ["Salaire", o.salaire || "Non précisé"],
    ["Télétravail", { complet: "Complet", partiel: "Partiel / hybride" }[o.teletravail] || "Non précisé"],
    ["Publiée le", date(o.date_publication)],
    ["Mise à jour", date(o.date_maj)],
    ["Repérée le", date(o.premiere_vue)],
  ];
  if (o.expiree) infos.push(["Expirée le", date(o.date_expiration)]);
  $("fiche-infos").replaceChildren(...infos.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));

  $("fiche-liens").replaceChildren(...(o.sources || []).map((s) => {
    const url = urlSure(s.url);
    const contenu = [
      `Voir l'offre sur ${s.nom}`,
      el("small", {}, `Source : ${s.nom} · mise à jour le ${date(s.date_maj)}${s.active === false ? " · n'est plus en ligne" : ""}`),
    ];
    return url
      ? el("a", { class: "lien-source", href: url, target: "_blank", rel: "noopener noreferrer" }, contenu)
      : el("div", { class: "lien-source" }, contenu);
  }));

  $("fiche-description").textContent = o.description || "Description non disponible.";
  $("fiche").showModal();
  $("fiche").scrollTop = 0;
}

// --- Branchements ------------------------------------------------------------

function brancher() {
  $("form-verrou").addEventListener("submit", async (e) => {
    e.preventDefault();
    const bouton = e.submitter || $("form-verrou").querySelector("button");
    bouton.disabled = true;
    $("erreur-verrou").hidden = true;
    try {
      await demarrer($("mdp").value, $("memoriser").checked);
    } catch (err) {
      $("erreur-verrou").textContent = err.message;
      $("erreur-verrou").hidden = false;
    } finally {
      bouton.disabled = false;
    }
  });

  $("verrouiller").addEventListener("click", () => {
    local.effacer(CLE_MDP);
    session.effacer(CLE_MDP);
    location.reload();
  });

  for (const id of ["f-texte", "f-contrat", "f-teletravail", "f-source", "f-nouvelles", "f-expirees"]) {
    $(id).addEventListener("input", afficherListe);
  }

  const fiche = $("fiche");
  fiche.addEventListener("click", (e) => {
    if (e.target === fiche || e.target.closest("[data-fermer]")) fiche.close();
  });
}

async function init() {
  brancher();
  const memorise = session.lire(CLE_MDP) || local.lire(CLE_MDP);
  if (memorise) {
    try {
      await demarrer(memorise, false);
      return;
    } catch (err) {
      if (err.message === "Mot de passe incorrect.") {
        local.effacer(CLE_MDP);
        session.effacer(CLE_MDP);
      } else {
        $("erreur-verrou").textContent = err.message;
        $("erreur-verrou").hidden = false;
      }
    }
  }
  $("ecran-verrou").hidden = false;
  $("mdp").focus();
}

init();
