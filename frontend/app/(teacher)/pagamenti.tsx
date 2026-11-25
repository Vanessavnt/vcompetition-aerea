import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Modal, TextInput,
  KeyboardAvoidingView, Platform, RefreshControl, Linking, Switch,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { theme } from "@/src/theme";
import { api, clearAuth } from "@/src/api";
import { useRouter } from "expo-router";

function statoInfo(stato: string) {
  switch (stato) {
    case "scaduto": return { color: theme.colors.error, label: "Scaduto" };
    case "in_scadenza": return { color: theme.colors.warning, label: "In scadenza" };
    case "attivo": return { color: theme.colors.success, label: "Attivo" };
    case "nessuno": return { color: theme.colors.muted, label: "Nessun abbonamento" };
    default: return { color: theme.colors.muted, label: "—" };
  }
}

export default function Pagamenti() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<"tutti" | "in_scadenza" | "scaduto" | "ritirata">("tutti");
  const [modal, setModal] = useState<any | null>(null);
  const [detail, setDetail] = useState<any | null>(null);
  const [pagamenti, setPagamenti] = useState<any[]>([]);
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [impModal, setImpModal] = useState(false);
  const [imp, setImp] = useState<any>({});
  const [form, setForm] = useState<any>({ tipo: "abbonamento", importo: "", data_pagamento: new Date().toISOString().slice(0, 10), scadenza: "", metodo: "contanti", note: "" });
  const router = useRouter();

  const load = useCallback(async () => {
    try { setData(await api.riepilogoPagamenti()); } catch (e) { console.log(e); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function apriDettaglio(a: any) {
    setDetail(a);
    try {
      const p = await api.listPagamenti(a.allievo_id);
      setPagamenti(p);
    } catch { setPagamenti([]); }
  }

  function openNewPagamento(a: any) {
    setForm({ tipo: "abbonamento", importo: "", data_pagamento: new Date().toISOString().slice(0, 10), scadenza: "", metodo: "contanti", note: "", allievo_id: a.allievo_id, allievo_name: `${a.nome} ${a.cognome}` });
    setModal({ mode: "new" });
  }

  function openRinnova(a: any) {
    const oggi = new Date();
    const oggiIso = oggi.toISOString().slice(0, 10);
    const scad = new Date(oggi.getTime() + 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const importoUltimo = a?.ultimo_abbonamento?.importo || "";
    const metodoUltimo = a?.ultimo_abbonamento?.metodo || "contanti";
    setForm({
      tipo: "abbonamento",
      importo: importoUltimo ? String(importoUltimo) : "",
      data_pagamento: oggiIso,
      scadenza: scad,
      metodo: metodoUltimo,
      note: "Rinnovo mensile",
      allievo_id: a.allievo_id,
      allievo_name: `${a.nome} ${a.cognome}`,
    });
    setModal({ mode: "rinnovo" });
  }

  async function cambiaStatoAllievoRitirata() {
    if (!detail) return;
    const nuovoStato = detail.stato_allievo === "ritirata" ? "attivo" : "ritirata";
    try {
      await api.updateAllievo(detail.allievo_id, {
        nome: detail.nome,
        cognome: detail.cognome,
        email: detail.email || null,
        telefono: detail.telefono || null,
        data_nascita: detail.data_nascita || "",
        certificato_scadenza: detail.certificato_scadenza || "",
        note: detail.note || "",
        foto: detail.foto || "",
        stato: nuovoStato,
        create_login: false,
        password: "",
      });
      const aggiornato = { ...detail, stato_allievo: nuovoStato };
      setDetail(aggiornato);
      const riepilogo = await api.riepilogoPagamenti();
      setData(riepilogo);
      showToast(nuovoStato === "ritirata" ? "Allieva/o segnata/o come ritirata/o" : "Allieva/o riattivata/o");
    } catch (e: any) {
      showToast(e.message || "Errore durante l'aggiornamento");
    }
  }

  async function save() {
    try {
      const body = { ...form, importo: parseFloat(form.importo) || 0 };
      delete body.allievo_name;
      await api.createPagamento(body);
      setModal(null);
      const upd = await api.riepilogoPagamenti();
      setData(upd);
      if (detail) {
        const p = await api.listPagamenti(detail.allievo_id);
        setPagamenti(p);
      }
    } catch (e: any) { console.log(e.message); }
  }

  async function logout() {
    await clearAuth();
    router.replace("/login");
  }

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  }

  function generaListeScadenze() {
    const allievi = data?.allievi ?? [];
    const oggi = new Date();
    const anno = oggi.getFullYear();
    const mese = oggi.getMonth();
    const giorni = new Date(anno, mese + 1, 0).getDate();
    const nomeMese = oggi.toLocaleDateString("it-IT", { month: "long", year: "numeric" });

    const pagine = Array.from({ length: giorni }, (_, i) => {
      const giorno = String(i + 1).padStart(2, "0");
      const dataGiorno = `${anno}-${String(mese + 1).padStart(2, "0")}-${giorno}`;
      const righe = allievi
        .filter((a: any) => a.ultimo_abbonamento?.scadenza?.slice(0, 10) === dataGiorno)
        .map((a: any) => `
          <tr>
            <td>${String(a.cognome ?? "").replace(/[&<>"]/g, "")} ${String(a.nome ?? "").replace(/[&<>"]/g, "")}</td>
            <td>${Number(a.ultimo_abbonamento?.importo ?? 0).toLocaleString("it-IT", { style: "currency", currency: "EUR" })}</td>
            <td class="check"></td>
          </tr>`).join("");

      return `<section class="pagina">
        <h1>VFIT — Scadenze del ${giorno}/${String(mese + 1).padStart(2, "0")}/${anno}</h1>
        <p class="mese">${nomeMese}</p>
        <table><thead><tr><th>Allievo</th><th>Importo</th><th>Pagato</th></tr></thead>
        <tbody>${righe || `<tr><td colspan="3" class="vuoto">Nessuna scadenza registrata per questo giorno</td></tr>`}</tbody></table>
        <p class="note">Note: __________________________________________________________</p>
      </section>`;
    }).join("");

    const html = `<!doctype html><html lang="it"><head><meta charset="utf-8">
      <title>Scadenze VFIT - ${nomeMese}</title>
      <style>
        @page { size: A4; margin: 18mm; }
        body { font-family: Arial, sans-serif; color: #222; }
        .pagina { page-break-after: always; break-after: page; }
        .pagina:last-child { page-break-after: auto; break-after: auto; }
        h1 { font-size: 21px; margin-bottom: 4px; }
        .mese { color: #555; text-transform: capitalize; margin-top: 0; }
        table { width: 100%; border-collapse: collapse; margin-top: 22px; }
        th, td { border: 1px solid #888; padding: 10px 8px; text-align: left; }
        th { background: #eee; }
        .check { width: 70px; height: 28px; }
        .vuoto { height: 40px; color: #666; }
        .note { margin-top: 28px; font-size: 12px; }
        @media screen { body { max-width: 800px; margin: 24px auto; } .pagina { padding: 20px; } }
      </style></head><body>${pagine}
      <script>window.onload = () => window.print();</script></body></html>`;

    const finestra = window.open("", "_blank");
    if (!finestra) {
      showToast("Consenti i popup per stampare le liste.");
      return;
    }
    finestra.document.open();
    finestra.document.write(html);
    finestra.document.close();
  }

  async function inviaPromemoriaBulk() {
    if (sending) return;
    setSending(true);
    try {
      const r = await api.inviaPromemoriaTutti();
      if (r.totale_inviati === 0) showToast("Nessun promemoria da inviare");
      else showToast(`${r.totale_inviati} promemoria inviati`);
    } catch (e: any) { showToast(e.message || "Errore invio"); }
    finally { setSending(false); }
  }

  async function inviaPromemoriaSingolo(allievo_id: string) {
    if (sending) return;
    setSending(true);
    try {
      await api.inviaPromemoria(allievo_id);
      showToast("Promemoria inviato via email");
    } catch (e: any) { showToast(e.message || "Errore invio"); }
    finally { setSending(false); }
  }

  function normalizzaTelefono(tel: string): string {
    let t = tel.replace(/[^\d+]/g, "");
    if (t.startsWith("+")) return t.slice(1);
    if (t.startsWith("00")) return t.slice(2);
    if (t.startsWith("39")) return t;
    // Numero italiano senza prefisso: aggiungi 39
    return "39" + t.replace(/^0+/, "");
  }

  async function apriWhatsApp(a: any) {
    if (!a?.telefono) { showToast("Numero di telefono mancante"); return; }
    const scad = a.ultimo_abbonamento?.scadenza || "";
    const stato = a.stato;
    let dettagliPag = "";
    if (imp?.iban) dettagliPag += `\n\n💳 IBAN: ${imp.iban}${imp.intestatario ? ` (${imp.intestatario})` : ""}`;
    if (imp?.sumup_link) dettagliPag += `\n🔗 SumUp: ${imp.sumup_link}`;
    if (imp?.paypal_link) dettagliPag += `\n🔗 PayPal: ${imp.paypal_link}`;
    if (imp?.accetta_contanti) dettagliPag += `\n💵 Contanti: ${imp?.indirizzo || "presso la segreteria"}`;
    const msg = stato === "scaduto"
      ? `Ciao ${a.nome}! 💃 Ti ricordiamo che il tuo abbonamento presso ${imp?.nome_scuola || "VFIT"} è scaduto il ${scad}. Ti aspettiamo per il rinnovo!${dettagliPag}`
      : `Ciao ${a.nome}! 💃 Ti ricordiamo che il tuo abbonamento presso ${imp?.nome_scuola || "VFIT"} scade il ${scad}. Rinnova per non perdere le prossime lezioni!${dettagliPag}`;
    const numero = normalizzaTelefono(a.telefono);
    const url = `https://wa.me/${numero}?text=${encodeURIComponent(msg)}`;
    try {
      const ok = await Linking.canOpenURL(url);
      if (!ok) { showToast("WhatsApp non disponibile"); return; }
      await Linking.openURL(url);
    } catch { showToast("Errore apertura WhatsApp"); }
  }

  async function inviaRicevutaEmail(p: any) {
    if (sending) return;
    setSending(true);
    try {
      await api.inviaRicevuta(p.id);
      showToast("Ricevuta inviata via email");
    } catch (e: any) { showToast(e.message || "Errore invio"); }
    finally { setSending(false); }
  }

  async function inviaRicevutaWhatsApp(p: any) {
    if (!detail?.telefono) { showToast("Numero di telefono mancante"); return; }
    const tipoL = p.tipo === "abbonamento" ? "abbonamento" : "lezione singola";
    const scadRow = p.scadenza ? `\n📅 Scadenza: ${p.scadenza}` : "";
    const msg = `✅ Pagamento ricevuto - ${imp?.nome_scuola || "VFIT"}\n\nCiao ${detail.nome}, confermiamo il tuo pagamento:\n\n💰 Importo: € ${p.importo.toFixed(2)}\n📄 Tipo: ${tipoL}\n📆 Data: ${p.data_pagamento}${scadRow}\n💳 Metodo: ${p.metodo || "contanti"}\n\nConserva questo messaggio come ricevuta. A presto! 💃`;
    const numero = normalizzaTelefono(detail.telefono);
    const url = `https://wa.me/${numero}?text=${encodeURIComponent(msg)}`;
    try {
      const ok = await Linking.canOpenURL(url);
      if (!ok) { showToast("WhatsApp non disponibile"); return; }
      await Linking.openURL(url);
    } catch { showToast("Errore apertura WhatsApp"); }
  }

  async function caricaImpostazioni() {
    try { setImp(await api.getImpostazioni()); } catch { }
  }

  async function salvaImpostazioni() {
    try {
      const upd = await api.updateImpostazioni(imp);
      setImp(upd);
      setImpModal(false);
      showToast("Impostazioni salvate");
    } catch (e: any) { showToast(e.message || "Errore salvataggio"); }
  }

  useEffect(() => { caricaImpostazioni(); }, []);

  if (loading || !data) {
    return <SafeAreaView style={styles.safe}><ActivityIndicator color={theme.colors.brand} style={{ marginTop: 40 }} /></SafeAreaView>;
  }

  const filtered = data.allievi.filter((a: any) => {
    if (filter === "tutti") return true;
    if (filter === "ritirata") return a.stato_allievo === "ritirata";
    return a.stato === filter && a.stato_allievo !== "ritirata";
  });

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.hLabel}>Contabilità</Text>
          <Text style={styles.hTitle}>Pagamenti</Text>
        </View>
        <View style={{ flexDirection: "row", gap: 8 }}>
          <Pressable testID="apri-crediti" onPress={() => router.push("/crediti")} style={styles.mailBtn}>
            <Feather name="award" size={18} color={theme.colors.brand} />
          </Pressable>
          <Pressable testID="apri-staff" onPress={() => router.push("/staff")} style={styles.mailBtn}>
            <Feather name="user-check" size={18} color={theme.colors.brand} />
          </Pressable>
          <Pressable testID="apri-report" onPress={() => router.push("/report")} style={styles.mailBtn}>
            <Feather name="bar-chart-2" size={18} color={theme.colors.brand} />
          </Pressable>
          <Pressable testID="apri-buoni" onPress={() => router.push("/buoni")} style={styles.mailBtn}>
            <Feather name="gift" size={18} color={theme.colors.brand} />
          </Pressable>
          <Pressable testID="open-impostazioni" onPress={() => setImpModal(true)} style={styles.mailBtn}>
            <Feather name="settings" size={18} color={theme.colors.brand} />
          </Pressable>
          <Pressable testID="invia-promemoria-tutti" onPress={inviaPromemoriaBulk} style={styles.mailBtn} disabled={sending}>
            {sending ? <ActivityIndicator color={theme.colors.brand} size="small" /> : <Feather name="mail" size={18} color={theme.colors.brand} />}
          </Pressable>
          <Pressable testID="logout-button" onPress={logout} style={styles.logout}>
            <Feather name="log-out" size={18} color={theme.colors.muted} />
          </Pressable>
        </View>
      </View>

      <Pressable
        testID="genera-liste-scadenze"
        onPress={generaListeScadenze}
        style={{ marginHorizontal: 16, marginTop: 12, marginBottom: 8, padding: 14, borderRadius: 10, backgroundColor: theme.colors.brand, alignItems: "center" }}
      >
        <Text style={{ color: "#fff", fontWeight: "700" }}>Genera PDF scadenze del mese</Text>
      </Pressable>

      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={theme.colors.brand} />}
      >
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.statsRow}>
          <View style={[styles.statCard, { backgroundColor: theme.colors.brand }]}>
            <Text style={[styles.statLabel, { color: "#fff", opacity: 0.85 }]}>Totale incassato</Text>
            <Text style={[styles.statValue, { color: "#fff" }]}>€ {data.totale_incassato.toFixed(2)}</Text>
          </View>
          <View style={styles.statCard}>
            <Text style={styles.statLabel}>In scadenza</Text>
            <Text style={[styles.statValue, { color: theme.colors.warning }]}>{data.in_scadenza}</Text>
          </View>
          <View style={styles.statCard}>
            <Text style={styles.statLabel}>Scaduti</Text>
            <Text style={[styles.statValue, { color: theme.colors.error }]}>{data.scaduti}</Text>
          </View>
        </ScrollView>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipsRow}>
          {([["tutti", "Tutti"], ["in_scadenza", "In scadenza"], ["scaduto", "Scaduti"], ["ritirata", "Ritirate"]] as const).map(([k, l]) => (
            <Pressable key={k} testID={`filter-${k}`} onPress={() => setFilter(k)} style={[styles.chip, filter === k && styles.chipOn]}>
              <Text style={[styles.chipT, filter === k && styles.chipTOn]}>{l}</Text>
            </Pressable>
          ))}
        </ScrollView>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Allievi</Text>
          {filtered.length === 0 ? (
            <Text style={styles.emptyT}>Nessun allievo in questa categoria</Text>
          ) : filtered.map((a: any) => {
            const info = statoInfo(a.stato);
            return (
              <Pressable key={a.allievo_id} testID={`allievo-pay-${a.allievo_id}`} onPress={() => apriDettaglio(a)} style={styles.row}>
                <View>
                  <Text style={styles.rowName}>{a.nome} {a.cognome}</Text>
                  <Text style={styles.rowMeta}>
                    {a.stato_allievo === "ritirata" ? "Allieva ritirata" : (a.ultimo_abbonamento ? `Scad: ${a.ultimo_abbonamento.scadenza}` : "Nessun abbonamento")}
                    {a.totale_pagato ? `  ·  € ${a.totale_pagato.toFixed(2)}` : ""}
                  </Text>
                </View>
                <View style={styles.statusBadge}>
                  {a.stato_allievo === "ritirata" ? (
                    <Text style={[styles.statusT, { color: theme.colors.error, fontWeight: "800" }]}>RITIRATA</Text>
                  ) : (
                    <>
                      <View style={[styles.dot, { backgroundColor: info.color }]} />
                      <Text style={[styles.statusT, { color: info.color }]}>{info.label}</Text>
                    </>
                  )}
                </View>
              </Pressable>
            );
          })}
        </View>
      </ScrollView>

      {/* Detail modal */}
      <Modal visible={!!detail} transparent animationType="slide" onRequestClose={() => { setDetail(null); setPagamenti([]); }}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
              <Text style={styles.modalTitle}>{detail?.nome} {detail?.cognome}</Text>
              <Pressable onPress={() => { setDetail(null); setPagamenti([]); }}><Feather name="x" size={22} color={theme.colors.muted} /></Pressable>
            </View>
            <Pressable
              testID="toggle-ritirata-allievo"
              onPress={cambiaStatoAllievoRitirata}
              style={[styles.btnPrimary, { marginBottom: 8, backgroundColor: detail?.stato_allievo === "ritirata" ? theme.colors.success : theme.colors.error }]}
            >
              <Text style={styles.btnPrimaryT}>{detail?.stato_allievo === "ritirata" ? "Riattiva allieva/o" : "Ritira allieva/o"}</Text>
            </Pressable>
            <Pressable testID="new-pagamento" onPress={() => openNewPagamento(detail)} style={[styles.btnPrimary, { marginBottom: 8 }]}>
              <Text style={styles.btnPrimaryT}>+ Nuovo pagamento</Text>
            </Pressable>
            {detail?.ultimo_abbonamento && (
              <Pressable testID="rinnova-abbonamento" onPress={() => openRinnova(detail)} style={[styles.btnRinnova, { marginBottom: 8 }]}>
                <Feather name="refresh-cw" size={16} color="#fff" />
                <Text style={styles.btnPrimaryT}>Rinnova abbonamento (+ 30 giorni)</Text>
              </Pressable>
            )}
            {(detail?.stato === "in_scadenza" || detail?.stato === "scaduto") && (
              <View style={{ gap: 8, marginBottom: 16 }}>
                <Pressable
                  testID="invia-promemoria-singolo"
                  onPress={() => inviaPromemoriaSingolo(detail.allievo_id)}
                  style={styles.btnMailPromo}
                  disabled={sending}
                >
                  {sending ? <ActivityIndicator color={theme.colors.brand} size="small" /> : (
                    <>
                      <Feather name="mail" size={16} color={theme.colors.brand} />
                      <Text style={styles.btnMailPromoT}>Invia promemoria via email</Text>
                    </>
                  )}
                </Pressable>
                {detail?.telefono ? (
                  <Pressable
                    testID="invia-whatsapp-singolo"
                    onPress={() => apriWhatsApp(detail)}
                    style={styles.btnWhatsapp}
                  >
                    <Feather name="message-circle" size={16} color="#fff" />
                    <Text style={styles.btnWhatsappT}>Invia promemoria via WhatsApp</Text>
                  </Pressable>
                ) : null}
              </View>
            )}
            <ScrollView style={{ maxHeight: 360 }}>
              {pagamenti.length === 0 ? (
                <Text style={styles.emptyT}>Nessun pagamento registrato</Text>
              ) : pagamenti.map(p => {
                const info = statoInfo(p.stato || "saldato");
                return (
                  <View key={p.id} style={styles.pagRow}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.pagTipo}>{p.tipo === "abbonamento" ? "Abbonamento" : "Lezione singola"}</Text>
                      <Text style={styles.pagMeta}>Pagato: {p.data_pagamento}{p.scadenza ? ` · Scad: ${p.scadenza}` : ""}</Text>
                      <Text style={styles.pagMeta}>{p.metodo}{p.note ? ` · ${p.note}` : ""}</Text>
                      <View style={styles.receiptActions}>
                        {detail?.email && (
                          <Pressable testID={`ricevuta-email-${p.id}`} onPress={() => inviaRicevutaEmail(p)} style={styles.receiptBtn} disabled={sending}>
                            <Feather name="mail" size={11} color={theme.colors.brand} />
                            <Text style={styles.receiptT}>Ricevuta email</Text>
                          </Pressable>
                        )}
                        {detail?.telefono && (
                          <Pressable testID={`ricevuta-wa-${p.id}`} onPress={() => inviaRicevutaWhatsApp(p)} style={[styles.receiptBtn, styles.receiptWa]}>
                            <Feather name="message-circle" size={11} color="#fff" />
                            <Text style={[styles.receiptT, { color: "#fff" }]}>Ricevuta WhatsApp</Text>
                          </Pressable>
                        )}
                      </View>
                    </View>
                    <View style={{ alignItems: "flex-end" }}>
                      <Text style={styles.pagAmt}>€ {p.importo.toFixed(2)}</Text>
                      {p.tipo === "abbonamento" && (
                        <Text style={[styles.pagState, { color: info.color }]}>{info.label}</Text>
                      )}
                      <Pressable onPress={async () => { await api.deletePagamento(p.id); const upd = await api.listPagamenti(detail.allievo_id); setPagamenti(upd); const r = await api.riepilogoPagamenti(); setData(r); }}>
                        <Feather name="trash-2" size={14} color={theme.colors.error} style={{ marginTop: 4 }} />
                      </Pressable>
                    </View>
                  </View>
                );
              })}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* New pagamento modal */}
      <Modal visible={!!modal} transparent animationType="slide" onRequestClose={() => setModal(null)}>
        <View style={styles.modalOverlay}>
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
            <ScrollView style={styles.modalCard} keyboardShouldPersistTaps="handled">
              <Text style={styles.modalTitle}>{modal?.mode === "rinnovo" ? "Rinnova abbonamento" : "Nuovo pagamento"}</Text>
              <Text style={styles.modalSub}>{form.allievo_name}{modal?.mode === "rinnovo" ? " · dati precompilati" : ""}</Text>

              <Text style={styles.label}>Tipo</Text>
              <View style={styles.pillRow}>
                {(["abbonamento", "lezione_singola"] as const).map(t => (
                  <Pressable key={t} onPress={() => setForm({ ...form, tipo: t })} style={[styles.pill, form.tipo === t && styles.pillOn]}>
                    <Text style={[styles.pillT, form.tipo === t && styles.pillTOn]}>{t === "abbonamento" ? "Abbonamento" : "Lezione singola"}</Text>
                  </Pressable>
                ))}
              </View>

              <Text style={styles.label}>Importo (€)</Text>
              <TextInput testID="importo-input" style={styles.input} value={form.importo} onChangeText={(v) => setForm({ ...form, importo: v.replace(/[^0-9.]/g, "") })} keyboardType="decimal-pad" placeholder="0.00" />

              <Text style={styles.label}>Data pagamento</Text>
              <TextInput style={styles.input} value={form.data_pagamento} onChangeText={(v) => setForm({ ...form, data_pagamento: v })} placeholder="YYYY-MM-DD" />

              {form.tipo === "abbonamento" && (
                <>
                  <Text style={styles.label}>Scadenza abbonamento</Text>
                  <TextInput testID="scadenza-input" style={styles.input} value={form.scadenza} onChangeText={(v) => setForm({ ...form, scadenza: v })} placeholder="YYYY-MM-DD" />
                </>
              )}

              <Text style={styles.label}>Metodo</Text>
              <View style={styles.pillRow}>
                {["contanti", "bonifico", "carta"].map(m => (
                  <Pressable key={m} onPress={() => setForm({ ...form, metodo: m })} style={[styles.pill, form.metodo === m && styles.pillOn]}>
                    <Text style={[styles.pillT, form.metodo === m && styles.pillTOn]}>{m}</Text>
                  </Pressable>
                ))}
              </View>

              <Text style={styles.label}>Note</Text>
              <TextInput style={styles.input} value={form.note} onChangeText={(v) => setForm({ ...form, note: v })} />

              <View style={styles.modalActions}>
                <Pressable onPress={() => setModal(null)} style={styles.btnGhost}>
                  <Text style={styles.btnGhostT}>Annulla</Text>
                </Pressable>
                <Pressable testID="save-pagamento" onPress={save} style={styles.btnPrimary}>
                  <Text style={styles.btnPrimaryT}>Salva</Text>
                </Pressable>
              </View>
              <View style={{ height: 40 }} />
            </ScrollView>
          </KeyboardAvoidingView>
        </View>
      </Modal>

      {/* Impostazioni scuola modal */}
      <Modal visible={impModal} transparent animationType="slide" onRequestClose={() => setImpModal(false)}>
        <View style={styles.modalOverlay}>
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
            <ScrollView style={styles.modalCard} keyboardShouldPersistTaps="handled">
              <Text style={styles.modalTitle}>Impostazioni scuola</Text>
              <Text style={styles.modalSub}>Questi dati appaiono nelle email e nei promemoria WhatsApp</Text>

              <Text style={styles.label}>Nome scuola</Text>
              <TextInput style={styles.input} value={imp?.nome_scuola || ""} onChangeText={(v) => setImp({ ...imp, nome_scuola: v })} />

              <Text style={styles.label}>Intestatario conto</Text>
              <TextInput style={styles.input} value={imp?.intestatario || ""} onChangeText={(v) => setImp({ ...imp, intestatario: v })} placeholder="es. VFIT SSD" />

              <Text style={styles.label}>IBAN</Text>
              <TextInput testID="imp-iban" style={styles.input} value={imp?.iban || ""} onChangeText={(v) => setImp({ ...imp, iban: v })} placeholder="IT60 X054 2811 1010 0000 0123 456" autoCapitalize="characters" />

              <Text style={styles.label}>Link SumUp</Text>
              <TextInput testID="imp-sumup" style={styles.input} value={imp?.sumup_link || ""} onChangeText={(v) => setImp({ ...imp, sumup_link: v })} placeholder="https://sumup.link/..." autoCapitalize="none" keyboardType="url" />

              <Text style={styles.label}>Link PayPal</Text>
              <TextInput style={styles.input} value={imp?.paypal_link || ""} onChangeText={(v) => setImp({ ...imp, paypal_link: v })} placeholder="https://paypal.me/..." autoCapitalize="none" keyboardType="url" />

              <Text style={styles.label}>Email di contatto</Text>
              <TextInput style={styles.input} value={imp?.contatto_email || ""} onChangeText={(v) => setImp({ ...imp, contatto_email: v })} autoCapitalize="none" keyboardType="email-address" />

              <Text style={styles.label}>Telefono di contatto</Text>
              <TextInput style={styles.input} value={imp?.contatto_telefono || ""} onChangeText={(v) => setImp({ ...imp, contatto_telefono: v })} keyboardType="phone-pad" />

              <Text style={styles.label}>Sito web</Text>
              <TextInput testID="imp-sito" style={styles.input} value={imp?.sito_web || ""} onChangeText={(v) => setImp({ ...imp, sito_web: v })} placeholder="https://www.vfitmilazzo.it" autoCapitalize="none" keyboardType="url" />
              {imp?.sito_web ? (
                <Pressable onPress={() => Linking.openURL(imp.sito_web)} style={styles.linkVisit}>
                  <Feather name="external-link" size={14} color={theme.colors.brand} />
                  <Text style={styles.linkVisitT}>Apri il sito</Text>
                </Pressable>
              ) : null}

              <View style={styles.contantiRow}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>💵 Accetta contanti in sede</Text>
                  <Text style={styles.contantiHint}>Include l'opzione nei messaggi ai clienti</Text>
                </View>
                <Switch value={imp?.accetta_contanti !== false} onValueChange={(v) => setImp({ ...imp, accetta_contanti: v })} trackColor={{ true: theme.colors.brand }} />
              </View>

              <Text style={styles.label}>Indirizzo (per pagamenti in contanti)</Text>
              <TextInput style={styles.input} value={imp?.indirizzo || ""} onChangeText={(v) => setImp({ ...imp, indirizzo: v })} placeholder="Via Colonnello Berté 53/55/57, Milazzo" />

              <View style={styles.modalActions}>
                <Pressable onPress={() => setImpModal(false)} style={styles.btnGhost}>
                  <Text style={styles.btnGhostT}>Annulla</Text>
                </Pressable>
                <Pressable testID="save-impostazioni" onPress={salvaImpostazioni} style={styles.btnPrimary}>
                  <Text style={styles.btnPrimaryT}>Salva</Text>
                </Pressable>
              </View>
              <View style={{ height: 40 }} />
            </ScrollView>
          </KeyboardAvoidingView>
        </View>
      </Modal>

      {toast && (
        <View style={styles.toast} testID="toast-msg">
          <Text style={styles.toastT}>{toast}</Text>
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colors.surface },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingHorizontal: theme.spacing.xl, paddingTop: theme.spacing.md, paddingBottom: theme.spacing.md },
  hLabel: { fontSize: 12, color: theme.colors.muted, letterSpacing: 1, textTransform: "uppercase" },
  hTitle: { fontSize: 30, color: theme.colors.onSurface, fontWeight: "300" },
  logout: { padding: 10, borderRadius: 20, borderWidth: 1, borderColor: theme.colors.border },
  mailBtn: { padding: 10, borderRadius: 20, borderWidth: 1, borderColor: theme.colors.brand, backgroundColor: theme.colors.brandTertiary },
  btnMailPromo: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, padding: 12, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.colors.brand, backgroundColor: theme.colors.brandTertiary },
  btnMailPromoT: { color: theme.colors.brand, fontWeight: "700", fontSize: 14 },
  btnWhatsapp: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, padding: 12, borderRadius: theme.radius.md, backgroundColor: "#25D366" },
  btnWhatsappT: { color: "#fff", fontWeight: "700", fontSize: 14 },
  statsRow: { paddingHorizontal: theme.spacing.xl, gap: 12, paddingBottom: theme.spacing.md },
  statCard: { padding: 16, borderRadius: theme.radius.lg, backgroundColor: theme.colors.surfaceSecondary, borderWidth: 1, borderColor: theme.colors.border, minWidth: 150 },
  statLabel: { fontSize: 11, color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 0.5 },
  statValue: { fontSize: 22, fontWeight: "700", color: theme.colors.onSurface, marginTop: 6 },
  chipsRow: { paddingHorizontal: theme.spacing.xl, gap: 8, paddingBottom: theme.spacing.md },
  chip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.colors.border, backgroundColor: theme.colors.surfaceSecondary, flexShrink: 0 },
  chipOn: { backgroundColor: theme.colors.brand, borderColor: theme.colors.brand },
  chipT: { fontSize: 12, color: theme.colors.onSurface, fontWeight: "500" },
  chipTOn: { color: "#fff", fontWeight: "700" },
  section: { paddingHorizontal: theme.spacing.xl, marginTop: theme.spacing.sm },
  sectionTitle: { fontSize: 12, color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: theme.spacing.sm },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: theme.colors.divider },
  rowName: { fontSize: 15, color: theme.colors.onSurface, fontWeight: "500" },
  rowMeta: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  statusBadge: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  statusT: { fontSize: 12, fontWeight: "600" },
  emptyT: { fontSize: 14, color: theme.colors.muted, textAlign: "center", padding: 20 },
  modalOverlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "flex-end" },
  modalCard: { backgroundColor: theme.colors.surface, padding: theme.spacing.xl, borderTopLeftRadius: 24, borderTopRightRadius: 24, maxHeight: "92%" },
  modalTitle: { fontSize: 22, color: theme.colors.onSurface, fontWeight: "600" },
  modalSub: { fontSize: 13, color: theme.colors.muted, marginTop: 4, marginBottom: theme.spacing.md },
  label: { fontSize: 11, color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: theme.spacing.xs, marginTop: theme.spacing.md },
  input: { borderWidth: 1, borderColor: theme.colors.border, borderRadius: theme.radius.md, padding: 12, fontSize: 15, color: theme.colors.onSurface, backgroundColor: theme.colors.surfaceSecondary },
  pillRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  pill: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.colors.border },
  pillOn: { backgroundColor: theme.colors.brand, borderColor: theme.colors.brand },
  pillT: { fontSize: 12, color: theme.colors.onSurface, fontWeight: "500" },
  pillTOn: { color: "#fff", fontWeight: "700" },
  modalActions: { flexDirection: "row", gap: 12, marginTop: theme.spacing.xl },
  btnGhost: { flex: 1, padding: 12, alignItems: "center", borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.colors.border },
  btnGhostT: { color: theme.colors.onSurface, fontWeight: "600" },
  btnPrimary: { padding: 12, alignItems: "center", borderRadius: theme.radius.md, backgroundColor: theme.colors.brand },
  btnRinnova: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, padding: 12, borderRadius: theme.radius.md, backgroundColor: theme.colors.success },
  btnPrimaryT: { color: "#fff", fontWeight: "700" },
  pagRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: theme.colors.divider, gap: 12 },
  pagTipo: { fontSize: 14, color: theme.colors.onSurface, fontWeight: "600" },
  pagMeta: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  pagAmt: { fontSize: 16, color: theme.colors.onSurface, fontWeight: "700" },
  pagState: { fontSize: 11, fontWeight: "600", marginTop: 2 },
  receiptActions: { flexDirection: "row", gap: 6, marginTop: 8, flexWrap: "wrap" },
  receiptBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.colors.brand, backgroundColor: theme.colors.brandTertiary },
  receiptWa: { backgroundColor: "#25D366", borderColor: "#25D366" },
  receiptT: { fontSize: 10, fontWeight: "700", color: theme.colors.brand },
  linkVisit: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8, alignSelf: "flex-start", paddingHorizontal: 10, paddingVertical: 6, backgroundColor: theme.colors.brandTertiary, borderRadius: theme.radius.pill },
  linkVisitT: { fontSize: 12, color: theme.colors.brand, fontWeight: "700" },
  contantiRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 16, padding: 12, backgroundColor: theme.colors.brandTertiary, borderRadius: theme.radius.md },
  contantiHint: { fontSize: 10, color: theme.colors.muted, marginTop: 2 },
  toast: { position: "absolute", top: 60, alignSelf: "center", backgroundColor: theme.colors.surfaceInverse, paddingVertical: 10, paddingHorizontal: 18, borderRadius: theme.radius.pill, elevation: 6 },
  toastT: { color: theme.colors.onSurfaceInverse, fontSize: 13, fontWeight: "600" },
});
