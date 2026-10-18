"use strict";

// ---------------------------------------------------------------------------
// Onglet « Carte » : les offres affichées (mêmes filtres que la liste) placées
// sur une carte OpenStreetMap avec Leaflet (copié dans docs/vendor/).
// Les points sont regroupés quand ils sont proches ; un clic ouvre la fiche.
// ---------------------------------------------------------------------------

const Carte = (() => {
  const COULEURS = { fort: "#0e7a4f", moyen: "#c98a00", faible: "#7a8494" };
  let carte = null;
  let groupe = null;

  function initialiser() {
    const rayon = 30; // km, pour le cercle de la zone de recherche
    carte = L.map("carte", { scrollWheelZoom: true }).setView(etat.centre, 10);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(carte);
    L.circle(etat.centre, { radius: rayon * 1000, color: "#2456d6", weight: 1, fillOpacity: 0.04, interactive: false }).addTo(carte);
    groupe = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 40 });
    carte.addLayer(groupe);
  }

  function bulle(o) {
    return el("div", { class: "bulle" },
      el("strong", {}, o.titre),
      el("div", { class: "discret" }, [o.entreprise || "Entreprise non précisée", o.lieu].filter(Boolean).join(" · ")),
      el("div", { class: "badges" }, badges(o).slice(0, 3)),
      el("button", { type: "button", class: "bouton-principal", onclick: () => ouvrirFiche(o) }, "Voir la fiche"),
    );
  }

  function afficher(offres) {
    if (!carte) initialiser();
    // La carte vient d'apparaître : Leaflet doit recalculer sa taille.
    setTimeout(() => carte.invalidateSize(), 0);
    groupe.clearLayers();
    const situees = offres.filter((o) => Array.isArray(o.coordonnees));
    for (const o of situees) {
      const couleur = COULEURS[classeScore(o.score ?? 0)];
      L.circleMarker(o.coordonnees, { radius: 8, color: "#ffffff", weight: 2, fillColor: couleur, fillOpacity: 0.95 })
        .bindPopup(() => bulle(o), { maxWidth: 280 })
        .addTo(groupe);
    }
    const sans = offres.length - situees.length;
    $("compteur").textContent = `${situees.length} offre${situees.length > 1 ? "s" : ""} sur la carte`
      + (sans ? ` · ${sans} sans localisation (voir la liste)` : "");
  }

  return { afficher };
})();
