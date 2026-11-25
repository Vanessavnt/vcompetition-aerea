import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Modal, TextInput,
  KeyboardAvoidingView, Platform, Switch, RefreshControl, Image, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import { theme } from "@/src/theme";
import { api } from "@/src/api";
import { AnnunciBanner } from "@/src/components/AnnunciBanner";

function certStato(d?: string): { color: string; label: string; icon: any } | null {
  if (!d) return null;
  try {
    const dd = new Date(d + "T00:00:00");
    const diff = Math.ceil((dd.getTime() - Date.now()) / (24 * 3600 * 1000));
    if (diff < 0) return { color: theme.colors.error, label: "Scaduto", icon: "alert-triangle" };
    if (diff <= 30) return { color: theme.colors.warning, label: `Scade in ${diff}g`, icon: "alert-circle" };
    return null;
  } catch { return null; }
}

export default function Allievi() {
  const [allievi, setAllievi] = useState<any[]>([]);
  const [ospiti, setOspiti] = useState<any[]>([]);
  const [compleanni, setCompleanni] = useState<any>({ oggi: [], mese: [] });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState<any | null>(null);
  const [ospitiModal, setOspitiModal] = useState(false);
  const [compleModal, setCompleModal] = useState(false);
  const [form, setForm] = useState<any>({});

  const load = useCallback(async () => {
    try {
      const [a, o, c] = await Promise.all([
        api.listAllievi(),
        api.listOspiti("in_attesa").catch(() => []),
        api.compleanni().catch(() => ({ oggi: [], mese: [] })),
      ]);
      setAllievi(a); setOspiti(o); setCompleanni(c);
    } catch (e) { console.log(e); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const filtered = allievi.filter(a => {
    const q = search.toLowerCase();
    return !q || `${a.nome} ${a.cognome}`.toLowerCase().includes(q) || (a.email || "").toLowerCase().includes(q);
  });

  function openNew() {
    setForm({ nome: "", cognome: "", email: "", telefono: "", data_nascita: "", certificato_scadenza: "", foto: "", stato: "attivo", create_login: false, password: "" });
    setModal({ mode: "new" });
  }
function openEdit(a: any) { setForm({ ...a, data_nascita: isoToItalian(a.data_nascita), certificato_scadenza: isoToItalian(a.certificato_scadenza), create_login: false, password: "" }); setModal({ mode: "edit", id: a.id }); }
  function italianToIso(value: string) {
  const m = value.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : value;
}

function isoToItalian(value?: string) {
  if (!value) return "";
  const m = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : value; 
} async function save() {
    try {const datiDaSalvare = { ...form, email: form.email?.trim() || null, telefono: form.telefono?.trim() || null, data_nascita: italianToIso(form.data_nascita || ""), certificato_scadenza: italianToIso(form.certificato_scadenza || "") };
      if (!form.nome || !form.cognome || !form.telefono) return;
      if (modal.mode === "new") await api.createAllievo(datiDaSalvare);
      else await api.updateAllievo(modal.id, datiDaSalvare);
      setModal(null); load();
    } catch (e: any) { console.log(e.message); }
  }
  async function remove(id: string) { await api.deleteAllievo(id); setModal(null); load(); }

  async function pickPhoto() {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) return;
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, quality: 0.5, base64: true, allowsEditing: true, aspect: [1, 1] });
    if (!res.canceled && res.assets[0].base64) {
      setForm({ ...form, foto: `data:image/jpeg;base64,${res.assets[0].base64}` });
    }
  }

  async function confermaOspite(id: string) { await api.confermaOspite(id); load(); }
  async function rifiutaOspite(id: string) { await api.rifiutaOspite(id); load(); }

  function auguriWhatsApp(a: any) {
    if (!a.telefono) return;
    const msg = `🎉 Tanti auguri di buon compleanno ${a.nome}! 💃🎂 Ti aspettiamo in sala per festeggiare! - VFIT`;
    const num = a.telefono.replace(/[^\d+]/g, "").replace(/^\+/, "").replace(/^00/, "");
    const finalNum = num.startsWith("39") ? num : "39" + num.replace(/^0+/, "");
    Linking.openURL(`https://wa.me/${finalNum}?text=${encodeURIComponent(msg)}`);
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.hLabel}>Anagrafica</Text>
          <Text style={styles.hTitle}>Allievi</Text>
        </View>
        <View style={{ flexDirection: "row", gap: 6 }}>
          {compleanni.mese?.length > 0 && (
            <Pressable testID="btn-compleanni" onPress={() => setCompleModal(true)} style={styles.iconBtn}>
              <Feather name="gift" size={18} color={theme.colors.brand} />
              {compleanni.oggi?.length > 0 && <View style={styles.badgeRed}><Text style={styles.badgeT}>{compleanni.oggi.length}</Text></View>}
            </Pressable>
          )}
          {ospiti.length > 0 && (
            <Pressable testID="btn-ospiti" onPress={() => setOspitiModal(true)} style={styles.iconBtn}>
              <Feather name="user-plus" size={18} color={theme.colors.brand} />
              <View style={styles.badgeRed}><Text style={styles.badgeT}>{ospiti.length}</Text></View>
            </Pressable>
          )}
        </View>
      </View>

      <AnnunciBanner />

      <View style={styles.searchBox}>
        <Feather name="search" size={16} color={theme.colors.muted} />
        <TextInput testID="search-allievi" value={search} onChangeText={setSearch} placeholder="Cerca allievo..." placeholderTextColor={theme.colors.muted} style={styles.searchInput} />
      </View>

      {loading ? <ActivityIndicator color={theme.colors.brand} style={{ marginTop: 40 }} /> : (
        <ScrollView contentContainerStyle={styles.list} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={theme.colors.brand} />}>
          {filtered.length === 0 ? (
            <View style={styles.empty}><Feather name="users" size={40} color={theme.colors.borderStrong} /><Text style={styles.emptyT}>Nessun allievo</Text></View>
          ) : filtered.map(a => {
            const cert = certStato(a.certificato_scadenza);
            return (
              <Pressable key={a.id} testID={`allievo-${a.id}`} style={styles.row} onPress={() => openEdit(a)}>
                {a.foto ? <Image source={{ uri: a.foto }} style={styles.avatarImg} /> : (
                  <View style={styles.avatar}><Text style={styles.avatarT}>{(a.nome[0] || "").toUpperCase()}{(a.cognome[0] || "").toUpperCase()}</Text></View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={styles.name}>{a.nome} {a.cognome}</Text>
                  {a.stato === "ritirata" ? <View style={styles.withdrawnBadge}><Text style={styles.withdrawnBadgeText}>RITIRATA</Text></View> : null}
                  <Text style={styles.meta}>{a.email || a.telefono || "—"}</Text>
                  {cert && (
                    <View style={styles.certBadge}>
                      <Feather name={cert.icon} size={11} color={cert.color} />
                      <Text style={[styles.certT, { color: cert.color }]}>Cert. medico {cert.label}</Text>
                    </View>
                  )}
                </View>
                <Feather name="chevron-right" size={18} color={theme.colors.muted} />
              </Pressable>
            );
          })}
        </ScrollView>
      )}

      <Pressable testID="add-allievo-fab" style={styles.fab} onPress={openNew}><Feather name="plus" size={24} color="#fff" /></Pressable>

      {/* Modal edit allievo */}
      <Modal visible={!!modal} transparent animationType="slide" onRequestClose={() => setModal(null)}>
        <View style={styles.overlay}>
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
            <ScrollView style={styles.mCard} keyboardShouldPersistTaps="handled">
              <Text style={styles.mTitle}>{modal?.mode === "new" ? "Nuovo allievo" : "Modifica allievo"}</Text>
              <Pressable onPress={pickPhoto} style={styles.photoBox}>
                {form.foto ? <Image source={{ uri: form.foto }} style={styles.photoImg} /> : (
                  <View style={styles.photoPlaceholder}><Feather name="camera" size={26} color={theme.colors.brand} /><Text style={styles.photoT}>Aggiungi foto</Text></View>
                )}
              </Pressable>
              <Text style={styles.label}>Nome *</Text>
              <TextInput style={styles.input} value={form.nome} onChangeText={(v) => setForm({ ...form, nome: v })} />
              <Text style={styles.label}>Cognome *</Text>
              <TextInput style={styles.input} value={form.cognome} onChangeText={(v) => setForm({ ...form, cognome: v })} />
              <Text style={styles.label}>Email</Text>
              <TextInput style={styles.input} value={form.email || ""} onChangeText={(v) => setForm({ ...form, email: v })} autoCapitalize="none" keyboardType="email-address" />
              <Text style={styles.label}>Telefono</Text>
              <TextInput style={styles.input} value={form.telefono || ""} onChangeText={(v) => setForm({ ...form, telefono: v })} keyboardType="phone-pad" />
              <Text style={styles.label}>Data di nascita (GG/MM/AAAA)</Text>
              <TextInput style={styles.input} value={form.data_nascita || ""} onChangeText={(v) => setForm({ ...form, data_nascita: v })} placeholder="15/05/1990" />
              <Text style={styles.label}>Scadenza certificato medico</Text>
              <TextInput testID="cert-scadenza" style={styles.input} value={form.certificato_scadenza || ""} onChangeText={(v) => setForm({ ...form, certificato_scadenza: v })} placeholder="GG/MM/AAAA" />

              {modal?.mode === "edit" && (
                <View style={styles.statusSection}>
                  <Text style={styles.label}>Stato allieva/o</Text>
                  <View style={{ flexDirection: "row", gap: 8 }}>
                    <Pressable onPress={() => setForm({ ...form, stato: "attivo" })} style={[styles.statusOption, form.stato !== "ritirata" && styles.statusOptionActive]}>
                      <Text style={[styles.statusOptionText, form.stato !== "ritirata" && styles.statusOptionTextActive]}>Attiva/o</Text>
                    </Pressable>
                    <Pressable onPress={() => setForm({ ...form, stato: "ritirata" })} style={[styles.statusOption, form.stato === "ritirata" && styles.statusWithdrawnActive]}>
                      <Text style={[styles.statusOptionText, form.stato === "ritirata" && styles.statusOptionTextActive]}>Ritirata/o</Text>
                    </Pressable>
                  </View>
                </View>
              )}

              {modal?.mode === "new" && (
                <>
                  <View style={styles.switchRow}>
                    <Text style={styles.label}>Crea accesso allievo</Text>
                    <Switch value={form.create_login} onValueChange={(v) => setForm({ ...form, create_login: v })} trackColor={{ true: theme.colors.brand }} />
                  </View>
                  {form.create_login && (
                    <>
                      <Text style={styles.label}>Password iniziale</Text>
                      <TextInput style={styles.input} secureTextEntry value={form.password || ""} onChangeText={(v) => setForm({ ...form, password: v })} />
                    </>
                  )}
                  <Text style={styles.label}>🤝 Codice referral usato (opzionale)</Text>
                  <TextInput
                    testID="codice-referral-usato"
                    style={styles.input}
                    value={form.codice_referral_usato || ""}
                    onChangeText={(v) => setForm({ ...form, codice_referral_usato: v.toUpperCase() })}
                    placeholder="es. VFITABC123"
                    autoCapitalize="characters"
                  />
                </>
              )}
              <View style={styles.actions}>
                {modal?.mode === "edit" && <Pressable onPress={() => remove(modal.id)} style={[styles.btn, { borderColor: theme.colors.error }]}><Text style={{ color: theme.colors.error, fontWeight: "700" }}>Elimina</Text></Pressable>}
                <Pressable onPress={() => setModal(null)} style={styles.btn}><Text style={{ fontWeight: "700" }}>Annulla</Text></Pressable>
                <Pressable testID="save-allievo" onPress={save} style={styles.btnPrimary}><Text style={{ color: "#fff", fontWeight: "700" }}>Salva</Text></Pressable>
              </View>
              <View style={{ height: 40 }} />
            </ScrollView>
          </KeyboardAvoidingView>
        </View>
      </Modal>

      {/* Ospiti modal */}
      <Modal visible={ospitiModal} transparent animationType="slide" onRequestClose={() => setOspitiModal(false)}>
        <View style={styles.overlay}>
          <View style={styles.mCard}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
              <Text style={styles.mTitle}>Richieste ospiti</Text>
              <Pressable onPress={() => setOspitiModal(false)}><Feather name="x" size={22} color={theme.colors.muted} /></Pressable>
            </View>
            <ScrollView style={{ maxHeight: 480 }}>
              {ospiti.map(o => (
                <View key={o.id} style={styles.ospiteRow}>
                  <Text style={styles.ospiteName}>{o.nome} {o.cognome}</Text>
                  <Text style={styles.ospiteMeta}>{o.email} · {o.telefono}</Text>
                  <Text style={styles.ospiteLez}>{o.lezione_info}</Text>
                  {o.note ? <Text style={styles.ospiteNote}>"{o.note}"</Text> : null}
                  <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
                    <Pressable onPress={() => confermaOspite(o.id)} style={styles.confBtn}><Feather name="check" size={12} color="#fff" /><Text style={styles.confT}>Conferma</Text></Pressable>
                    <Pressable onPress={() => rifiutaOspite(o.id)} style={styles.rejBtn}><Text style={styles.rejT}>Rifiuta</Text></Pressable>
                    {o.telefono && <Pressable onPress={() => Linking.openURL(`https://wa.me/${o.telefono.replace(/[^\d]/g, "").replace(/^0+/, "39")}`)} style={styles.waBtn}><Feather name="message-circle" size={12} color="#fff" /></Pressable>}
                  </View>
                </View>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* Compleanni modal */}
      <Modal visible={compleModal} transparent animationType="slide" onRequestClose={() => setCompleModal(false)}>
        <View style={styles.overlay}>
          <View style={styles.mCard}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
              <Text style={styles.mTitle}>🎂 Compleanni</Text>
              <Pressable onPress={() => setCompleModal(false)}><Feather name="x" size={22} color={theme.colors.muted} /></Pressable>
            </View>
            {compleanni.oggi?.length > 0 && (
              <>
                <Text style={styles.subT}>Oggi</Text>
                {compleanni.oggi.map((a: any) => (
                  <View key={a.id} style={styles.birthRow}>
                    <Text style={styles.birthName}>{a.nome} {a.cognome} · {a.eta_oggi} anni</Text>
                    {a.telefono && <Pressable onPress={() => auguriWhatsApp(a)} style={styles.confBtn}><Feather name="message-circle" size={12} color="#fff" /><Text style={styles.confT}>Auguri WhatsApp</Text></Pressable>}
                  </View>
                ))}
              </>
            )}
            <Text style={styles.subT}>Questo mese</Text>
            <ScrollView style={{ maxHeight: 400 }}>
              {compleanni.mese?.map((a: any) => (
                <View key={a.id} style={styles.birthRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.birthName}>{a.nome} {a.cognome}</Text>
                    <Text style={styles.birthMeta}>Giorno {a.giorno} · {a.eta_prossima} anni</Text>
                  </View>
                </View>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colors.surface },
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.spacing.xl, paddingTop: theme.spacing.md, paddingBottom: theme.spacing.sm, gap: 8 },
  hLabel: { fontSize: 12, color: theme.colors.muted, letterSpacing: 1, textTransform: "uppercase" },
  hTitle: { fontSize: 30, color: theme.colors.onSurface, fontWeight: "300" },
  iconBtn: { position: "relative", padding: 10, borderRadius: 20, borderWidth: 1, borderColor: theme.colors.brand, backgroundColor: theme.colors.brandTertiary },
  badgeRed: { position: "absolute", top: -4, right: -4, backgroundColor: theme.colors.error, minWidth: 18, height: 18, borderRadius: 9, alignItems: "center", justifyContent: "center", paddingHorizontal: 4 },
  badgeT: { color: "#fff", fontSize: 10, fontWeight: "800" },
  searchBox: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: theme.colors.surfaceTertiary, marginHorizontal: theme.spacing.xl, borderRadius: theme.radius.md, paddingHorizontal: 12, marginBottom: theme.spacing.md },
  searchInput: { flex: 1, paddingVertical: 10, color: theme.colors.onSurface, fontSize: 14 },
  list: { paddingBottom: 100 },
  empty: { alignItems: "center", padding: 40, gap: 10 },
  emptyT: { color: theme.colors.muted },
  row: { flexDirection: "row", alignItems: "center", paddingVertical: 12, paddingHorizontal: theme.spacing.xl, borderBottomWidth: 1, borderBottomColor: theme.colors.divider, gap: 12 },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: theme.colors.brandTertiary, alignItems: "center", justifyContent: "center" },
  avatarImg: { width: 44, height: 44, borderRadius: 22 },
  avatarT: { color: theme.colors.onBrandTertiary, fontWeight: "700", fontSize: 14 },
  name: { fontSize: 16, color: theme.colors.onSurface, fontWeight: "500" },
  meta: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  certBadge: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 4 },
  certT: { fontSize: 10, fontWeight: "700" },
  withdrawnBadge: { alignSelf: "flex-start", marginTop: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6, backgroundColor: "#FCE8E8" },
  withdrawnBadgeText: { color: "#B42318", fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  statusSection: { marginTop: 8, marginBottom: 8 },
  statusOption: { flex: 1, alignItems: "center", paddingVertical: 11, borderRadius: 9, borderWidth: 1, borderColor: theme.colors.border },
  statusOptionActive: { backgroundColor: theme.colors.brandTertiary, borderColor: theme.colors.brand },
  statusWithdrawnActive: { backgroundColor: "#FCE8E8", borderColor: "#B42318" },
  statusOptionText: { color: theme.colors.muted, fontWeight: "700" },
  statusOptionTextActive: { color: theme.colors.onSurface },
  fab: { position: "absolute", right: 20, bottom: 20, width: 56, height: 56, borderRadius: 28, backgroundColor: theme.colors.brand, alignItems: "center", justifyContent: "center", elevation: 6 },
  overlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "flex-end" },
  mCard: { backgroundColor: theme.colors.surface, padding: theme.spacing.xl, borderTopLeftRadius: 24, borderTopRightRadius: 24, maxHeight: "92%" },
  mTitle: { fontSize: 22, fontWeight: "600", color: theme.colors.onSurface, marginBottom: 12 },
  subT: { fontSize: 11, color: theme.colors.brand, textTransform: "uppercase", letterSpacing: 1, fontWeight: "800", marginTop: 12, marginBottom: 6 },
  photoBox: { alignSelf: "center", marginBottom: 8 },
  photoImg: { width: 100, height: 100, borderRadius: 50 },
  photoPlaceholder: { width: 100, height: 100, borderRadius: 50, borderWidth: 2, borderColor: theme.colors.brand, borderStyle: "dashed", alignItems: "center", justifyContent: "center", backgroundColor: theme.colors.brandTertiary },
  photoT: { fontSize: 10, color: theme.colors.brand, fontWeight: "700", marginTop: 2 },
  label: { fontSize: 11, color: theme.colors.muted, textTransform: "uppercase", letterSpacing: 1, marginTop: 10, marginBottom: 4 },
  input: { borderWidth: 1, borderColor: theme.colors.border, borderRadius: theme.radius.md, padding: 12, fontSize: 15, color: theme.colors.onSurface, backgroundColor: theme.colors.surfaceSecondary },
  switchRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 12 },
  actions: { flexDirection: "row", gap: 8, marginTop: 20 },
  btn: { flex: 1, padding: 12, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.colors.border, alignItems: "center" },
  btnPrimary: { flex: 1, padding: 12, borderRadius: theme.radius.md, backgroundColor: theme.colors.brand, alignItems: "center" },
  ospiteRow: { padding: 12, borderBottomWidth: 1, borderBottomColor: theme.colors.divider },
  ospiteName: { fontSize: 15, fontWeight: "700", color: theme.colors.onSurface },
  ospiteMeta: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  ospiteLez: { fontSize: 13, color: theme.colors.brand, fontWeight: "600", marginTop: 4 },
  ospiteNote: { fontSize: 12, color: theme.colors.muted, fontStyle: "italic", marginTop: 4 },
  confBtn: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: theme.colors.success, paddingHorizontal: 10, paddingVertical: 6, borderRadius: theme.radius.pill },
  confT: { color: "#fff", fontSize: 11, fontWeight: "700" },
  rejBtn: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.colors.error },
  rejT: { color: theme.colors.error, fontSize: 11, fontWeight: "700" },
  waBtn: { backgroundColor: "#25D366", paddingHorizontal: 10, paddingVertical: 6, borderRadius: theme.radius.pill },
  birthRow: { flexDirection: "row", alignItems: "center", padding: 12, borderBottomWidth: 1, borderBottomColor: theme.colors.divider, gap: 8 },
  birthName: { fontSize: 14, color: theme.colors.onSurface, fontWeight: "600" },
  birthMeta: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
});
