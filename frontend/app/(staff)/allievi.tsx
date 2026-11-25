import { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, RefreshControl, Image, TextInput, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { theme } from "@/src/theme";
import { api, clearAuth } from "@/src/api";
import { useRouter } from "expo-router";

export default function StaffAllievi() {
  const [allievi, setAllievi] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [q, setQ] = useState("");
  const router = useRouter();

  const load = useCallback(async () => {
    try { setAllievi(await api.staffMieiAllievi()); }
    catch (e: any) { console.log(e.message); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function logout() { await clearAuth(); router.replace("/login"); }

  const filtered = q.trim()
    ? allievi.filter((a: any) => `${a.nome} ${a.cognome}`.toLowerCase().includes(q.toLowerCase()))
    : allievi;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.hLabel}>I miei corsi</Text>
          <Text style={styles.hTitle}>Allievi</Text>
        </View>
        <Pressable onPress={logout} style={styles.logout} testID="staff-logout">
          <Feather name="log-out" size={18} color={theme.colors.muted} />
        </Pressable>
      </View>

      <View style={{ paddingHorizontal: theme.spacing.xl, marginBottom: 8 }}>
        <View style={styles.searchBox}>
          <Feather name="search" size={16} color={theme.colors.muted} />
          <TextInput style={styles.search} value={q} onChangeText={setQ} placeholder="Cerca allievo..." placeholderTextColor={theme.colors.muted} />
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.colors.brand} style={{ marginTop: 40 }} />
      ) : (
        <ScrollView
          contentContainerStyle={{ paddingBottom: 60 }}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={theme.colors.brand} />}
        >
          {filtered.length === 0 ? (
            <View style={styles.empty}>
              <Feather name="users" size={40} color={theme.colors.muted} />
              <Text style={styles.emptyT}>
                {allievi.length === 0
                  ? "Non hai ancora allievi assegnati.\nParla con Vanessa per essere assegnato ai corsi."
                  : "Nessun allievo trovato"}
              </Text>
            </View>
          ) : filtered.map((a: any) => (
            <View key={a.id} style={styles.row}>
              {a.foto ? <Image source={{ uri: a.foto }} style={styles.avatarImg} /> : (
                <View style={styles.avatar}><Text style={styles.avatarT}>{(a.nome?.[0] || "?").toUpperCase()}</Text></View>
              )}
              <View style={{ flex: 1 }}>
                <Text style={styles.name}>{a.nome} {a.cognome}</Text>
                {a.stato === "ritirata" ? <View style={styles.withdrawnBadge}><Text style={styles.withdrawnBadgeText}>RITIRATA</Text></View> : null}
                {a.email ? <Text style={styles.meta}>{a.email}</Text> : null}
                {a.telefono ? <Text style={styles.meta}>{a.telefono}</Text> : null}
              </View>
              {a.telefono ? (
                <Pressable onPress={() => Linking.openURL(`tel:${a.telefono}`)} style={styles.iconBtn}>
                  <Feather name="phone" size={16} color={theme.colors.brand} />
                </Pressable>
              ) : null}
            </View>
          ))}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colors.surface },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingHorizontal: theme.spacing.xl, paddingTop: theme.spacing.md, paddingBottom: theme.spacing.md },
  hLabel: { fontSize: 12, color: theme.colors.muted, letterSpacing: 1, textTransform: "uppercase" },
  hTitle: { fontSize: 28, color: theme.colors.onSurface, fontWeight: "300" },
  logout: { padding: 10, borderRadius: 20, borderWidth: 1, borderColor: theme.colors.border },
  searchBox: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, backgroundColor: theme.colors.surfaceSecondary, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.colors.border },
  search: { flex: 1, padding: 10, fontSize: 14, color: theme.colors.onSurface },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: theme.spacing.xl, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: theme.colors.divider },
  avatarImg: { width: 42, height: 42, borderRadius: 21 },
  avatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: theme.colors.brand, alignItems: "center", justifyContent: "center" },
  avatarT: { color: "#fff", fontSize: 15, fontWeight: "700" },
  name: { fontSize: 15, fontWeight: "600", color: theme.colors.onSurface },
  withdrawnBadge: { alignSelf: "flex-start", marginTop: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6, backgroundColor: "#FCE8E8" },
  withdrawnBadgeText: { color: "#B42318", fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  meta: { fontSize: 12, color: theme.colors.muted, marginTop: 2 },
  iconBtn: { padding: 10, borderRadius: 20, borderWidth: 1, borderColor: theme.colors.brand, backgroundColor: theme.colors.brandTertiary },
  empty: { alignItems: "center", padding: 40 },
  emptyT: { fontSize: 14, color: theme.colors.muted, textAlign: "center", marginTop: 12, lineHeight: 20 },
});
