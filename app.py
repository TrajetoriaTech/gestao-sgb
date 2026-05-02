import streamlit as st
from ops import (
    registrar_feira_completa, registrar_fiado_avulso,
    registrar_pagamento_fiado, buscar_historico_cliente,
    excluir_definitivo, esvaziar_lixeira, atualizar_feira_completa,
    exportar_csv, _get_fator_quebra,
    calcular_metricas_historicas, simular_feira,
    registrar_extra, excluir_extra, buscar_extras_feira,
    calcular_saude_fiados, salvar_threshold_fiado, salvar_fator_quebra,
)
from database import Session, Feira, Compra, Cliente, HistoricoFiado, ExtraFeira, Configuracao
from importar import parsear_notion_csv, parsear_notas_iphone, importar_feiras
import pandas as pd

st.set_page_config(page_title="Gestão da Banca", layout="wide")
st.title("🥩 Sistema de Gestão da Banca")

# --- Session State ---
for k, v in {
    'form_reset_count': 0,
    'fiado_reset_count': 0,
    'edit_reset_count': 0,
    'lista_gados_temp': [],
    'lista_extras_temp': [],
    'edit_feira_id': None,
    'lista_gados_edit': [],
    'bi_pagina_feiras': 0,    # paginação histórico detalhado (janela de 3 meses)
    'bi_pagina_mensal': 0,    # paginação tabela mensal (janela de 1 ano)
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

aba_bi, aba_previsao, aba_fiado, aba_registro, aba_importar, aba_gerenciar = st.tabs([
    "📊 Inteligência de Negócio", "🔮 Previsão", "🤝 Fiados", "📝 Registrar Feira", "📥 Importar Dados", "⚙️ Dados"
])

# ============================================================
# ABA BI
# ============================================================
with aba_bi:
    session = Session()
    try:
        fator_quebra = _get_fator_quebra(session)
        feiras = session.query(Feira).filter_by(ativo=1).all()

        if not feiras:
            st.info("Aguardando registros ativos para gerar o BI.")
        else:
            dados_bi = []
            for f in feiras:
                compras = session.query(Compra).filter_by(id_feira=f.id).all()
                total_kg = sum(float(c.peso_bruto) for c in compras)
                total_kg_liquido = total_kg * (1 - fator_quebra)
                custo_gado = round(sum((float(c.preco_arroba) / 15) * float(c.peso_bruto) for c in compras), 2)

                fiados_dia = sum(
                    float(h.valor) for h in
                    session.query(HistoricoFiado).filter_by(id_feira=f.id, tipo='DEBITO').all()
                )

                total_extras = sum(float(e.valor) for e in session.query(ExtraFeira).filter_by(id_feira=f.id).all())
                imposto = float(f.imposto) if f.imposto else 0

                v_caixa = float(f.caixa_out) + float(f.total_pix) + float(f.total_cartao)
                lucro_liq = round(v_caixa - float(f.caixa_in), 2)
                fat_real = round(lucro_liq + custo_gado + fiados_dia, 2)
                margem = round((lucro_liq / fat_real) * 100, 2) if fat_real > 0 else 0
                lucro_por_kg = round(lucro_liq / total_kg_liquido, 2) if total_kg_liquido > 0 else 0
                lucro_por_arroba = round(lucro_liq / (total_kg / 15), 2) if total_kg > 0 else 0
                lucro_so_carne = round(lucro_liq - total_extras, 2)

                dados_bi.append({
                    "ID": f.id,
                    "Data": f.data,
                    "Data_Label": f.data.strftime('%d/%m'),
                    "KG Total": round(total_kg, 1),
                    "KG Líquido": round(total_kg_liquido, 1),
                    "Arrobas": round(total_kg / 15, 2),
                    "Custo Gado (R$)": custo_gado,
                    "Imposto (R$)": imposto,
                    "Extras (R$)": round(total_extras, 2),
                    "Faturamento Real (R$)": fat_real,
                    "Lucro Líquido (R$)": lucro_liq,
                    "Lucro Só Carne (R$)": lucro_so_carne,
                    "Lucro/@ (R$)": lucro_por_arroba,
                    "Lucro/KG (R$)": lucro_por_kg,
                    "Margem (%)": margem,
                })

            df = pd.DataFrame(dados_bi)
            df['Data'] = pd.to_datetime(df['Data'])

            # --- Cards principais ---
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Vendas Totais", f"R$ {df['Faturamento Real (R$)'].sum():.2f}")
            c2.metric("Investimento Gado", f"R$ {df['Custo Gado (R$)'].sum():.2f}")
            c3.metric("Lucro Acumulado", f"R$ {df['Lucro Líquido (R$)'].sum():.2f}")
            c4.metric("Peso Total (kg)", f"{df['KG Total'].sum():.1f} kg")

            # --- Cards informativos ---
            c5, c6, c7 = st.columns(3)
            c5.metric("Total Impostos (info)", f"R$ {df['Imposto (R$)'].sum():.2f}",
                      help="Informativo — já descontado do caixa")
            c6.metric("Total Extras (info)", f"R$ {df['Extras (R$)'].sum():.2f}",
                      help="Fígado, bucho, mocotó etc. — já incluídos no caixa")
            c7.metric("Lucro Só da Carne", f"R$ {df['Lucro Só Carne (R$)'].sum():.2f}",
                      help="Lucro líquido descontando o valor dos extras")

            # ── PAINEL DE SAÚDE DOS FIADOS ──────────────────────────────
            st.divider()
            st.subheader("💰 Saúde Financeira — Caixa a Receber (Fiados)")

            saude = calcular_saude_fiados()

            pct = saude['percentual_fiado']
            threshold = saude['threshold']
            em_alerta = saude['em_alerta']

            # Linha de cards de saúde
            sf1, sf2, sf3, sf4 = st.columns(4)
            sf1.metric(
                "📥 Caixa a Receber",
                f"R$ {saude['total_em_aberto']:.2f}",
                help="Total pendente de todos os clientes com fiado em aberto"
            )
            sf2.metric(
                "📊 % do Faturamento em Fiado",
                f"{pct:.1f}%",
                delta=f"Limite: {threshold:.0f}%",
                delta_color="inverse" if em_alerta else "off",
                help="Quanto do seu faturamento histórico ainda não entrou no caixa"
            )
            sf3.metric(
                "👥 Clientes Devedores",
                saude['total_devedores'],
                help="Quantidade de clientes com saldo devedor ativo"
            )
            ticket_medio = (
                round(saude['total_em_aberto'] / saude['total_devedores'], 2)
                if saude['total_devedores'] > 0 else 0
            )
            sf4.metric(
                "🧾 Ticket Médio de Fiado",
                f"R$ {ticket_medio:.2f}",
                help="Valor médio de dívida por cliente"
            )

            # Alerta visual quando ultrapassa o threshold
            if em_alerta:
                st.warning(
                    f"⚠️ **Atenção:** O fiado pendente ({pct:.1f}%) ultrapassou o limite configurado de "
                    f"{threshold:.0f}% do faturamento. Considere acionar os clientes em aberto.",
                    icon="🚨"
                )
            else:
                st.success(
                    f"✅ Fiado em nível saudável: {pct:.1f}% do faturamento (limite: {threshold:.0f}%)"
                )

            # Aging — breakdown por faixa
            aging = saude['aging']
            st.markdown("**📅 Aging do Fiado (por tempo em aberto)**")
            ag1, ag2, ag3 = st.columns(3)
            ag1.metric("🟢 Até 30 dias", f"{aging['ate_30']} cliente(s)")
            ag2.metric("🟡 31–60 dias", f"{aging['de_31_a_60']} cliente(s)")
            ag3.metric("🔴 Acima de 60 dias", f"{aging['acima_60']} cliente(s)")

            # Tabela de aging detalhada
            if saude['detalhe_aging']:
                df_aging = pd.DataFrame(saude['detalhe_aging'])
                df_aging.columns = ['Cliente', 'Saldo Devedor (R$)', 'Dias em Aberto', 'Situação']
                df_aging['Saldo Devedor (R$)'] = df_aging['Saldo Devedor (R$)'].map(lambda x: f"R$ {x:.2f}")
                st.dataframe(df_aging, use_container_width=True, hide_index=True)

            # ── FIM PAINEL FIADOS ────────────────────────────────────────

            st.divider()

            # Gráfico custo vs lucro
            st.subheader("📊 Performance por Feira (Custo vs Lucro)")
            st.bar_chart(
                df.set_index("Data_Label")[["Custo Gado (R$)", "Lucro Líquido (R$)"]],
                color=["#FF4B4B", "#29B5E8"]
            )

            # Gráfico composição do lucro
            if df['Extras (R$)'].sum() > 0:
                st.subheader("🔍 Composição do Lucro: Carne vs Extras")
                st.bar_chart(
                    df.set_index("Data_Label")[["Lucro Só Carne (R$)", "Extras (R$)"]],
                    color=["#29B5E8", "#F4C542"]
                )

            # Scatter
            st.subheader("🎯 Eficiência: Peso vs Margem")
            st.scatter_chart(
                df.sort_values("Data"),
                x="KG Total", y="Margem (%)", size="Lucro Líquido (R$)", color="#29B5E8"
            )

            # Histórico detalhado — paginação por janela de 3 meses
            st.subheader("📋 Histórico Detalhado de Performance")
            tabela_view = df.copy().sort_values("Data", ascending=False)
            tabela_view['Data'] = tabela_view['Data'].dt.strftime('%d/%m/%Y')
            tabela_view = tabela_view.drop(columns=["Data_Label"])

            # Janelas de 3 meses a partir da mais recente
            datas_ord = sorted(df['Data'].dt.to_period('M').unique(), reverse=True)
            janelas = [datas_ord[i:i+3] for i in range(0, len(datas_ord), 3)]
            total_janelas = len(janelas)

            if total_janelas > 1:
                ph1, ph2, ph3 = st.columns([1, 6, 1])
                with ph1:
                    if st.button("◀", key="prev_feiras", disabled=st.session_state.bi_pagina_feiras == 0):
                        st.session_state.bi_pagina_feiras -= 1
                        st.rerun()
                with ph2:
                    janela_atual = janelas[st.session_state.bi_pagina_feiras]
                    label_ini = str(janela_atual[-1]).replace('/', '/')
                    label_fim = str(janela_atual[0]).replace('/', '/')
                    st.caption(f"Período: {label_ini} → {label_fim}  |  Página {st.session_state.bi_pagina_feiras + 1} de {total_janelas}")
                with ph3:
                    if st.button("▶", key="next_feiras", disabled=st.session_state.bi_pagina_feiras >= total_janelas - 1):
                        st.session_state.bi_pagina_feiras += 1
                        st.rerun()

                periodos_janela = [str(p) for p in janelas[st.session_state.bi_pagina_feiras]]
                # reconstrói coluna de período para filtrar
                tabela_view['_periodo'] = pd.to_datetime(tabela_view['Data'], format='%d/%m/%Y').dt.to_period('M').astype(str)
                tabela_filtrada = tabela_view[tabela_view['_periodo'].isin(periodos_janela)].drop(columns=['_periodo'])
            else:
                tabela_filtrada = tabela_view

            st.dataframe(tabela_filtrada, use_container_width=True, hide_index=True)

            # Análise mensal — paginação por ano
            st.divider()
            st.subheader("📅 Desempenho Consolidado por Mês")
            df['Mês/Ano'] = df['Data'].dt.strftime('%m/%Y')
            df_mensal = df.groupby('Mês/Ano').agg({
                'Faturamento Real (R$)': 'sum',
                'Custo Gado (R$)': 'sum',
                'Imposto (R$)': 'sum',
                'Extras (R$)': 'sum',
                'Lucro Líquido (R$)': 'sum',
                'Lucro Só Carne (R$)': 'sum',
                'KG Total': 'sum'
            }).reset_index()
            df_mensal['Lucro/KG Médio'] = round(df_mensal['Lucro Líquido (R$)'] / df_mensal['KG Total'], 2)

            # Agrupa meses por ano para paginação
            df_mensal['_ano'] = df_mensal['Mês/Ano'].str[-4:]
            anos_disp = sorted(df_mensal['_ano'].unique(), reverse=True)
            total_anos = len(anos_disp)

            if total_anos > 1:
                pm1, pm2, pm3 = st.columns([1, 6, 1])
                with pm1:
                    if st.button("◀", key="prev_mensal", disabled=st.session_state.bi_pagina_mensal == 0):
                        st.session_state.bi_pagina_mensal -= 1
                        st.rerun()
                with pm2:
                    ano_sel = anos_disp[st.session_state.bi_pagina_mensal]
                    st.caption(f"Ano: {ano_sel}  |  Página {st.session_state.bi_pagina_mensal + 1} de {total_anos}")
                with pm3:
                    if st.button("▶", key="next_mensal", disabled=st.session_state.bi_pagina_mensal >= total_anos - 1):
                        st.session_state.bi_pagina_mensal += 1
                        st.rerun()
                df_mensal_pag = df_mensal[df_mensal['_ano'] == anos_disp[st.session_state.bi_pagina_mensal]].drop(columns=['_ano'])
            else:
                df_mensal_pag = df_mensal.drop(columns=['_ano'])

            col_tab, col_gra = st.columns([2, 1])
            with col_tab:
                st.write("**Resumo Financeiro Mensal**")
                st.dataframe(df_mensal_pag, use_container_width=True, hide_index=True)
            with col_gra:
                st.write("**Evolução do Lucro Mensal**")
                st.line_chart(df_mensal_pag.set_index("Mês/Ano")["Lucro Líquido (R$)"])
    finally:
        session.close()

# ============================================================
# ABA PREVISÃO
# ============================================================
with aba_previsao:
    st.header("🔮 Previsão e Simulação de Feira")

    metricas = calcular_metricas_historicas()

    if not metricas:
        st.warning("Você precisa ter pelo menos uma feira registrada para usar a previsão.")
    else:
        st.info(
            f"Previsão baseada em {metricas['n_feiras']} feira(s)  ·  "
            f"Média histórica: {metricas['kg_medio_por_feira']:.0f} kg/feira  ·  "
            f"Preço @ médio: R$ {metricas['preco_arroba_medio']:.2f}  ·  "
            f"Margem média: {metricas['margem_media']:.1f}%  ·  "
            f"Extras médios: R$ {metricas['extras_medio']:.2f}/feira"
        )

        col_sim1, col_sim2 = st.columns(2)
        with col_sim1:
            peso_sim = st.number_input(
                "⚖️ Peso do gado (kg)", min_value=None, value=None,
                placeholder="Ex: 300", key="sim_peso"
            )
        with col_sim2:
            preco_sim = st.number_input(
                "💰 Preço por arroba (R$)", min_value=None, value=None,
                placeholder=f"Histórico: R$ {metricas['preco_arroba_medio']:.2f}",
                key="sim_preco"
            )

        if peso_sim and peso_sim > 0 and preco_sim and preco_sim > 0:
            sim = simular_feira(peso_sim, preco_sim, metricas)

            st.divider()
            st.subheader("📈 Resultado Projetado")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Custo do Gado", f"R$ {sim['custo']:.2f}")
            m2.metric("Faturamento Previsto", f"R$ {sim['faturamento']:.2f}")
            m3.metric("Lucro Previsto", f"R$ {sim['lucro']:.2f}")
            m4.metric("Margem Prevista", f"{sim['margem']}%")

            st.divider()
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                st.subheader("📦 Detalhes do Lote")
                st.markdown(f"""
| Item | Valor |
|------|-------|
| Peso bruto | {sim['peso_bruto']:.1f} kg |
| Peso líquido (−{metricas['fator_quebra']*100:.0f}% quebra) | {sim['peso_liquido']:.1f} kg |
| Arrobas | {sim['arrobas']:.2f} @ |
| Lucro por KG líquido | R$ {sim['lucro_por_kg']:.2f} |
| Extras esperados (média histórica) | R$ {metricas['extras_medio']:.2f} |
""")
            with col_d2:
                st.subheader("💳 Breakdown de Recebimento")
                st.markdown(f"""
| Forma | Valor Previsto |
|-------|---------------|
| 💵 Espécie | R$ {sim['especie_proj']:.2f} |
| 📱 Pix | R$ {sim['pix_proj']:.2f} |
| 💳 Cartão | R$ {sim['cartao_proj']:.2f} |
""")

            st.divider()
            st.subheader("📊 Simulação vs Média Histórica")
            df_comp = pd.DataFrame({
                'Indicador': ['Faturamento/kg', 'Lucro/kg', 'Margem (%)'],
                'Histórico': [
                    round(metricas['faturamento_por_kg'], 2),
                    round(metricas['lucro_por_kg'], 2),
                    round(metricas['margem_media'], 1)
                ],
                'Simulação': [
                    round(sim['faturamento'] / peso_sim, 2) if peso_sim > 0 else 0,
                    round(sim['lucro_por_kg'], 2),
                    sim['margem']
                ]
            })
            st.dataframe(df_comp, use_container_width=True, hide_index=True)
        else:
            st.caption("Preencha o peso e o preço por arroba para ver a simulação.")

# ============================================================
# ABA FIADOS
# ============================================================
with aba_fiado:
    st.header("🤝 Controle de Fiados")

    fid = st.session_state.fiado_reset_count

    # ── PAINEL RESUMO DE SAÚDE ───────────────────────────────
    saude = calcular_saude_fiados()

    pct = saude['percentual_fiado']
    threshold = saude['threshold']
    em_alerta = saude['em_alerta']

    if em_alerta:
        st.warning(
            f"⚠️ **Atenção:** {pct:.1f}% do faturamento em fiado pendente (limite: {threshold:.0f}%)",
            icon="🚨"
        )
    else:
        st.success(f"✅ Fiado em nível saudável: {pct:.1f}% do faturamento (limite: {threshold:.0f}%)")

    rs1, rs2, rs3, rs4 = st.columns(4)
    rs1.metric("📥 Total a Receber", f"R$ {saude['total_em_aberto']:.2f}")
    rs2.metric("% do Faturamento", f"{pct:.1f}%")
    rs3.metric("👥 Devedores", saude['total_devedores'])
    ticket_medio = (
        round(saude['total_em_aberto'] / saude['total_devedores'], 2)
        if saude['total_devedores'] > 0 else 0
    )
    rs4.metric("🧾 Ticket Médio", f"R$ {ticket_medio:.2f}")

    # Aging resumido
    aging = saude['aging']
    ag1, ag2, ag3 = st.columns(3)
    ag1.metric("🟢 Até 30 dias", f"{aging['ate_30']} cliente(s)")
    ag2.metric("🟡 31–60 dias", f"{aging['de_31_a_60']} cliente(s)")
    ag3.metric("🔴 Acima de 60 dias", f"{aging['acima_60']} cliente(s)")

    st.divider()
    # ── FIM PAINEL RESUMO ────────────────────────────────────

    with st.expander("➕ Lançar Novo Fiado", expanded=False):
        col_fa1, col_fa2 = st.columns(2)
        with col_fa1:
            nome_fiado_avulso = st.text_input("Nome do Cliente", key=f"fa_nome_{fid}")
            valor_fiado_avulso = st.number_input(
                "Valor (R$)", min_value=None, value=None,
                placeholder="Digite o valor...", key=f"fa_valor_{fid}"
            )
        with col_fa2:
            desc_fiado_avulso = st.text_input("Descrição (opcional)", key=f"fa_desc_{fid}")

        if st.button("💾 Registrar Fiado", type="primary"):
            if not nome_fiado_avulso.strip():
                st.error("Informe o nome do cliente.")
            elif not valor_fiado_avulso or valor_fiado_avulso <= 0:
                st.error("O valor deve ser maior que zero.")
            else:
                ok, msg = registrar_fiado_avulso(nome_fiado_avulso, valor_fiado_avulso, desc_fiado_avulso)
                if ok:
                    st.success(msg)
                    st.session_state.fiado_reset_count += 1
                    st.rerun()
                else:
                    st.error(msg)

    st.divider()

    session = Session()
    try:
        devedores = session.query(Cliente).filter(Cliente.saldo_devedor > 0).order_by(Cliente.nome).all()

        # Monta um dict rápido de aging por cliente para exibir junto ao expander
        aging_por_cliente = {d['nome']: d for d in saude['detalhe_aging']}

        if not devedores:
            st.info("Nenhum fiado pendente. ✅")
        else:
            for d in devedores:
                info_aging = aging_por_cliente.get(d.nome, {})
                faixa = info_aging.get('faixa', '')
                dias = info_aging.get('dias_em_aberto', 0)
                label_expander = (
                    f"👤 {d.nome}  —  Dívida: R$ {d.saldo_devedor:.2f}  |  {faixa}  ({dias}d)"
                )

                with st.expander(label_expander):
                    historico = buscar_historico_cliente(d.id)
                    movs = historico.get('movimentacoes', [])
                    total_devido = historico.get('total_devido', 0)
                    total_pago   = historico.get('total_pago', 0)

                    # Totais no topo do expander
                    hc1, hc2, hc3 = st.columns(3)
                    hc1.metric("📋 Total Original da Dívida", f"R$ {total_devido:.2f}",
                               help="Soma de todos os débitos lançados para este cliente")
                    hc2.metric("✅ Total Já Pago", f"R$ {total_pago:.2f}",
                               help="Soma de todos os pagamentos recebidos")
                    hc3.metric("⏳ Saldo Atual", f"R$ {float(d.saldo_devedor):.2f}",
                               help="Dívida restante = Total original − Total pago")

                    if movs:
                        st.markdown("**📜 Histórico de Movimentações**")
                        df_hist = pd.DataFrame(movs)
                        df_hist.columns = ['Data', 'Tipo', 'Valor (R$)', 'Descrição', 'ID Feira']
                        df_hist['Valor (R$)'] = df_hist['Valor (R$)'].map(lambda x: f"R$ {x:.2f}")
                        st.dataframe(
                            df_hist.drop(columns=['ID Feira']),
                            use_container_width=True, hide_index=True
                        )

                    st.markdown("**💵 Registrar Pagamento**")
                    valor_pag = st.number_input(
                        "Valor recebido (R$)", min_value=None, value=None,
                        placeholder="Digite o valor pago...",
                        max_value=float(d.saldo_devedor),
                        key=f"v_{d.id}"
                    )
                    if st.button("✅ Confirmar Pagamento", key=f"b_{d.id}"):
                        if not valor_pag or valor_pag <= 0:
                            st.error("Informe um valor válido.")
                        else:
                            ok = registrar_pagamento_fiado(d.id, valor_pag)
                            if ok:
                                st.success(f"Pagamento de R$ {valor_pag:.2f} registrado.")
                                st.rerun()
                            else:
                                st.error("Erro ao registrar pagamento.")
    finally:
        session.close()

# ============================================================
# ABA REGISTRAR FEIRA
# ============================================================
with aba_registro:
    st.header("📝 Nova Feira")
    rid = st.session_state.form_reset_count

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Dados do Caixa")
        data_f = st.date_input("Data da Feira", key=f"d_{rid}")
        c_in = st.number_input("Caixa Inicial (Troco)", min_value=None, value=None, placeholder="0,00", key=f"ci_{rid}")
        c_out = st.number_input("Caixa Final (Espécie)", min_value=None, value=None, placeholder="0,00", key=f"co_{rid}")
        pix = st.number_input("Total em Pix", min_value=None, value=None, placeholder="0,00", key=f"px_{rid}")
        cartao = st.number_input("Total em Cartão", min_value=None, value=None, placeholder="0,00", key=f"ct_{rid}")
        imposto_novo = st.number_input(
            "🧾 Imposto (R$)", min_value=None, value=None, placeholder="0,00", key=f"imp_{rid}",
            help="Informativo — valor já descontado do caixa antes do registro"
        )

    with col2:
        st.subheader("Adicionar Lotes de Gado")
        pg = st.number_input("Peso do Lote (kg)", min_value=None, value=None, placeholder="Ex: 146.5", key=f"pg_{rid}")
        ag = st.number_input("Preço da @ do Lote", min_value=None, value=None, placeholder="Ex: 220.0", key=f"ag_{rid}")
        sexo_g = st.radio("Sexo", ["Macho", "Fêmea"], horizontal=True, key=f"sx_{rid}")

        if st.button("➕ Adicionar Lote à Lista"):
            if pg and ag:
                st.session_state.lista_gados_temp.append({
                    'peso': pg, 'preco': ag,
                    'sexo': 'M' if sexo_g == 'Macho' else 'F'
                })
                st.toast("Lote adicionado!")
                st.rerun()
            else:
                st.warning("Preencha peso e preço para adicionar o lote.")

        if st.session_state.lista_gados_temp:
            st.write("**Lotes para esta feira:**")
            for i, g in enumerate(st.session_state.lista_gados_temp):
                sexo_label = "♂ Macho" if g.get('sexo', 'M') == 'M' else "♀ Fêmea"
                st.info(f"Lote {i+1}: {g['peso']} kg a R$ {g['preco']}/@ — {sexo_label}")
            if st.button("🗑 Limpar Lotes"):
                st.session_state.lista_gados_temp = []
                st.rerun()

    # --- Extras da feira (antes de salvar) ---
    st.divider()
    st.subheader("🍖 Extras desta Feira")
    st.caption("Fígado, bucho, mocotó etc. — informativo, valor já está no caixa.")
    col_ex1, col_ex2, col_ex3 = st.columns([2, 1, 1])
    with col_ex1:
        desc_extra_novo = st.text_input("Item", placeholder="Ex: Fígado, Bucho, Mocotó", key=f"ex_desc_{rid}")
    with col_ex2:
        valor_extra_novo = st.number_input("Valor (R$)", min_value=None, value=None, placeholder="Ex: 50.00", key=f"ex_val_{rid}")
    with col_ex3:
        st.write("")
        st.write("")
        if st.button("➕ Adicionar Extra"):
            if not desc_extra_novo.strip():
                st.error("Informe o nome do item.")
            elif not valor_extra_novo or valor_extra_novo <= 0:
                st.error("Informe um valor válido.")
            else:
                st.session_state.lista_extras_temp.append({
                    'descricao': desc_extra_novo.strip(),
                    'valor': valor_extra_novo
                })
                st.toast("Extra adicionado!")
                st.rerun()

    if st.session_state.lista_extras_temp:
        for i, e in enumerate(st.session_state.lista_extras_temp):
            col_ei, col_eb = st.columns([5, 1])
            col_ei.write(f"• {e['descricao']}: R$ {e['valor']:.2f}")
            if col_eb.button("🗑", key=f"del_extra_novo_{i}"):
                st.session_state.lista_extras_temp.pop(i)
                st.rerun()
        total_ex_novo = sum(e['valor'] for e in st.session_state.lista_extras_temp)
        st.info(f"Total de extras: **R$ {total_ex_novo:.2f}**")

    st.divider()
    if st.button("💾 SALVAR FEIRA COMPLETA", type="primary", use_container_width=True):
        caixa_preenchido = all([c_in is not None, c_out is not None, pix is not None, cartao is not None])
        if caixa_preenchido and st.session_state.lista_gados_temp:
            ok = registrar_feira_completa(
                data_f.strftime("%d/%m/%Y"),
                c_in, c_out, pix, cartao,
                imposto_novo or 0,
                st.session_state.lista_gados_temp,
                []
            )
            if ok:
                # Registra os extras vinculados à feira recém-criada
                if st.session_state.lista_extras_temp:
                    session_ex = Session()
                    try:
                        ultima_feira = session_ex.query(Feira).order_by(Feira.id.desc()).first()
                        if ultima_feira:
                            for e in st.session_state.lista_extras_temp:
                                registrar_extra(ultima_feira.id, e['descricao'], e['valor'])
                    finally:
                        session_ex.close()
                st.session_state.lista_gados_temp = []
                st.session_state.lista_extras_temp = []
                st.session_state.form_reset_count += 1
                st.success("Tudo salvo com sucesso!")
                st.rerun()
            else:
                st.error("Erro ao salvar. Verifique os dados e tente novamente.")
        else:
            st.error("Preencha todos os dados do caixa e adicione pelo menos um lote de gado.")


# ============================================================
# ABA IMPORTAR DADOS
# ============================================================
with aba_importar:
    st.header("📥 Importar Dados")

    modo = st.radio(
        "Fonte dos dados",
        ["📄 CSV do Notion", "📝 Bloco de Notas (texto livre)"],
        horizontal=True,
        key="import_modo"
    )

    st.divider()

    feiras_preview = []

    # ── NOTION CSV ──────────────────────────────────────────
    if modo == "📄 CSV do Notion":
        st.markdown(
            "Exporte sua tabela no Notion como **CSV** (menu ··· → Export → CSV) "
            "e faça o upload abaixo."
        )
        arquivo = st.file_uploader("Arquivo CSV do Notion", type=["csv"], key="notion_csv")

        if arquivo:
            with st.spinner("Lendo e interpretando o CSV..."):
                feiras_preview = parsear_notion_csv(arquivo.read())

    # ── BLOCO DE NOTAS ──────────────────────────────────────
    else:
        st.markdown(
            "Cole abaixo o texto do Bloco de Notas exatamente como está. "
            "Pode colar múltiplas feiras de uma vez."
        )

        col_notas1, col_notas2 = st.columns([3, 1])
        with col_notas2:
            preco_kg_dia = st.number_input(
                "💰 Preço do kg (R$)",
                min_value=0.0,
                value=0.0,
                step=0.5,
                format="%.2f",
                key="preco_kg_dia",
                help=(
                    "Usado para converter fiados anotados em kg (ex: '4,5kg fiado'). "
                    "Se deixar em 0, linhas com kg serão marcadas como pendentes para revisão manual."
                )
            )
            if preco_kg_dia > 0:
                st.caption(f"✅ 1 kg = R$ {preco_kg_dia:.2f}")
            else:
                st.caption("⚠️ Sem preço/kg — fiados em kg ficam pendentes.")

        with col_notas1:
            texto_notas = st.text_area(
                "Texto das anotações",
                height=240,
                placeholder="Domingo 05 de abril 2026\nPeso:\n111\n174\n\nPreço:\n280\n300\n\nCaixa in: 267,00 + 341,00\n...",
                key="notas_texto"
            )

        if st.button("🔍 Interpretar texto", type="primary"):
            if texto_notas.strip():
                with st.spinner("Interpretando anotações..."):
                    feiras_preview = parsear_notas_iphone(texto_notas, preco_kg_dia=preco_kg_dia)
                st.session_state['import_preview'] = feiras_preview
                # FIX: não limpa o texto aqui — só após importação confirmada
            else:
                st.warning("Cole o texto antes de interpretar.")

        if 'import_preview' in st.session_state and modo != "📄 CSV do Notion":
            feiras_preview = st.session_state['import_preview']

    # ── PRÉ-VISUALIZAÇÃO ────────────────────────────────────
    if feiras_preview:
        novas = [f for f in feiras_preview if not f['ja_existe']]
        existentes = [f for f in feiras_preview if f['ja_existe']]

        st.divider()
        col_res1, col_res2, col_res3 = st.columns(3)
        col_res1.metric("📋 Feiras encontradas", len(feiras_preview))
        col_res2.metric("✅ Novas (serão importadas)", len(novas))
        col_res3.metric("⏭️ Já existem (serão puladas)", len(existentes))

        if not novas:
            st.info("Todas as feiras encontradas já estão no banco. Nada a importar.")
        else:
            # Tabela de preview
            st.subheader("📋 Pré-visualização das feiras novas")
            rows_prev = []
            for f in novas:
                lotes_desc = " + ".join(
                    f"{l['peso']}kg@{l['preco']}" for l in f.get('lotes', [])
                ) or "⚠️ sem lote"
                fiados_n = len(f.get('fiados_detectados', []))
                fiados_revisao = sum(1 for fd in f.get('fiados_detectados', []) if fd.get('revisar'))
                rows_prev.append({
                    "Data": f['data'],
                    "Caixa IN": f"R$ {f['caixa_in']:.2f}",
                    "Caixa OUT": f"R$ {f['caixa_out']:.2f}",
                    "Pix": f"R$ {f['total_pix']:.2f}",
                    "Cartão": f"R$ {f['total_cartao']:.2f}",
                    "Lotes": lotes_desc,
                    "Fiados": fiados_n,
                    "⚠️ Revisão": fiados_revisao if fiados_revisao else "—",
                })
            st.dataframe(pd.DataFrame(rows_prev), use_container_width=True, hide_index=True)

            # Fiados detectados — expandível com coluna de revisão
            todos_fiados = [(f['data'], fd) for f in novas for fd in f.get('fiados_detectados', [])]
            n_revisao = sum(1 for _, fd in todos_fiados if fd.get('revisar'))

            if todos_fiados:
                label_exp = f"🤝 {len(todos_fiados)} movimentação(ões) detectada(s)"
                if n_revisao:
                    label_exp += f" — ⚠️ {n_revisao} precisam de revisão"
                with st.expander(label_exp + " — clique para revisar"):

                    if n_revisao:
                        st.warning(
                            f"⚠️ **{n_revisao} lançamento(s) marcados para revisão** — "
                            "eles **não serão importados** automaticamente. "
                            "Lance-os manualmente na aba Fiados após a importação.",
                            icon="✏️"
                        )

                    rows_fiad = []
                    for data_f, fd in todos_fiados:
                        revisar_flag = "⚠️ Revisar" if fd.get('revisar') else "✅ OK"
                        motivo = fd.get('motivo_revisao', '') or '—'
                        rows_fiad.append({
                            "Feira": data_f,
                            "Tipo": "💸 Débito" if fd['tipo'] == 'DEBITO' else "💰 Crédito",
                            "Nome": fd['nome'],
                            "Valor": f"R$ {fd['valor']:.2f}" if fd['valor'] else "—",
                            "Status": revisar_flag,
                            "Observação": motivo[:80],
                            "Descrição": fd.get('descricao', '')[:60],
                        })
                    st.dataframe(pd.DataFrame(rows_fiad), use_container_width=True, hide_index=True)

            n_fiados_ok = sum(
                1 for _, fd in todos_fiados
                if not fd.get('revisar') and fd.get('valor', 0) > 0
            )
            importar_fiados_toggle = st.toggle(
                f"Importar fiados confirmados automaticamente ({n_fiados_ok} OK / {n_revisao} pendentes)",
                value=False,
                help=(
                    "Importa apenas fiados com status ✅ OK e valor > 0. "
                    "Os marcados como ⚠️ Revisar são sempre pulados — lance-os manualmente."
                )
            )

            st.divider()
            if st.button(
                f"🚀 Importar {len(novas)} feira(s) agora",
                type="primary",
                use_container_width=True
            ):
                with st.spinner("Importando..."):
                    resultado = importar_feiras(novas, importar_fiados=importar_fiados_toggle)

                if resultado['importadas'] > 0:
                    st.success(
                        f"✅ {resultado['importadas']} feira(s) importada(s)! "
                        f"| Puladas: {resultado['puladas']} | Erros: {resultado['erros']}"
                    )
                    # FIX: reseta o preview e o texto após importação confirmada
                    if 'import_preview' in st.session_state:
                        del st.session_state['import_preview']
                    st.rerun()
                else:
                    st.error(
                        f"Nenhuma feira importada. "
                        f"Puladas: {resultado['puladas']} | Erros: {resultado['erros']}"
                    )


# ============================================================
# ABA DADOS
# ============================================================
with aba_gerenciar:
    st.header("⚙️ Gerenciamento de Dados")

    # Backup
    st.subheader("📥 Exportar Dados")
    if st.button("Gerar Backup Excel"):
        try:
            output = exportar_csv()
            st.download_button(
                label="⬇️ Baixar arquivo .xlsx",
                data=output,
                file_name="backup_banca.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        except Exception as e:
            st.error(f"Erro ao gerar backup: {e}")

    st.divider()

    # --- Configurações do sistema ---
    st.subheader("🔧 Configurações")
    session = Session()
    try:
        cfg_threshold = session.query(Configuracao).filter_by(chave='threshold_fiado').first()
        valor_threshold_atual = float(cfg_threshold.valor) * 100 if cfg_threshold else 15.0
        cfg_quebra = session.query(Configuracao).filter_by(chave='fator_quebra').first()
        valor_quebra_atual = float(cfg_quebra.valor) * 100 if cfg_quebra else 10.0
    finally:
        session.close()

    col_cfg1, col_cfg2 = st.columns(2)
    with col_cfg1:
        novo_threshold = st.number_input(
            "🚨 Limite de alerta de fiado (%)",
            min_value=1.0, max_value=100.0,
            value=valor_threshold_atual,
            step=1.0,
            help="Percentual do faturamento total em fiado que dispara o alerta. Default: 15%"
        )
        if st.button("💾 Salvar limite de fiado"):
            if salvar_threshold_fiado(novo_threshold / 100):
                st.success(f"Limite atualizado para {novo_threshold:.0f}%")
                st.rerun()
            else:
                st.error("Erro ao salvar.")
    with col_cfg2:
        nova_quebra = st.number_input(
            "⚖️ Fator de quebra (%)",
            min_value=0.0, max_value=50.0,
            value=valor_quebra_atual,
            step=0.5,
            help="Percentual de perda de peso no abate aplicado sobre o peso bruto. Default: 10%"
        )
        if st.button("💾 Salvar fator de quebra"):
            if salvar_fator_quebra(nova_quebra / 100):
                st.success(f"Fator de quebra atualizado para {nova_quebra:.1f}%")
                st.rerun()
            else:
                st.error("Erro ao salvar.")

    st.divider()

    session = Session()
    try:
        ativas = session.query(Feira).filter_by(ativo=1).order_by(Feira.data.desc()).all()
        apagadas = session.query(Feira).filter_by(ativo=0).order_by(Feira.data.desc()).all()

        col_del, col_rec, col_edit = st.columns([1, 1, 2])

        # Mover para lixeira
        with col_del:
            st.subheader("🗑 Mover para Lixeira")
            if ativas:
                ids_del = st.multiselect(
                    "Selecionar feiras para remover",
                    options=[f.id for f in ativas],
                    format_func=lambda fid: f"{next(f.data.strftime('%d/%m/%Y') for f in ativas if f.id == fid)} (ID {fid})",
                    key="excluir_seletor"
                )
                if st.button("Mover para Lixeira", type="primary", use_container_width=True):
                    if ids_del:
                        for id_del in ids_del:
                            f_obj = session.query(Feira).filter_by(id=id_del).first()
                            if f_obj:
                                f_obj.ativo = 0
                        session.commit()
                        st.rerun()
                    else:
                        st.warning("Selecione ao menos uma feira.")
            else:
                st.write("Não há registros ativos.")

        # Lixeira
        with col_rec:
            st.subheader("♻️ Lixeira")
            if apagadas:
                ids_selecionados = st.multiselect("Selecionar itens", options=[f.id for f in apagadas])
                c_btn1, c_btn2 = st.columns(2)
                if c_btn1.button("Restaurar", use_container_width=True):
                    if ids_selecionados:
                        for id_alvo in ids_selecionados:
                            f_obj = session.query(Feira).filter_by(id=id_alvo).first()
                            if f_obj:
                                f_obj.ativo = 1
                        session.commit()
                        st.rerun()
                if c_btn2.button("Apagar de Vez", type="primary", use_container_width=True):
                    if ids_selecionados:
                        for id_alvo in ids_selecionados:
                            excluir_definitivo(id_alvo)
                        st.rerun()
                st.divider()
                if st.button("🚨 ESVAZIAR TUDO", type="primary", use_container_width=True):
                    esvaziar_lixeira()
                    st.rerun()
            else:
                st.info("Lixeira vazia.")

        # Editar feira
        with col_edit:
            st.subheader("📝 Editar Registro")
            if ativas:
                id_edit = st.selectbox("ID para alteração", options=[f.id for f in ativas], key="edit_seletor")
                f_edit = session.query(Feira).filter_by(id=id_edit).first()

                if st.session_state.edit_feira_id != id_edit:
                    compras_atuais = session.query(Compra).filter_by(id_feira=id_edit).all()
                    st.session_state.lista_gados_edit = [
                        {'peso': float(c.peso_bruto), 'preco': float(c.preco_arroba), 'sexo': c.sexo or 'M'}
                        for c in compras_atuais
                    ]
                    st.session_state.edit_feira_id = id_edit
                    st.session_state.edit_reset_count += 1  # reseta keys ao trocar de feira

                if f_edit:
                    eid = st.session_state.edit_reset_count
                    v_in = float(f_edit.caixa_in)
                    v_out = float(f_edit.caixa_out)
                    v_pix = float(f_edit.total_pix)
                    v_car = float(f_edit.total_cartao)
                    v_imp = float(f_edit.imposto) if f_edit.imposto else 0.0

                    new_in  = st.number_input("Caixa Inicial", value=v_in, key=f"e_in_{eid}")
                    new_out = st.number_input("Caixa Espécie", value=v_out, key=f"e_out_{eid}")
                    new_pix = st.number_input("Pix", value=v_pix, key=f"e_pix_{eid}")
                    new_car = st.number_input("Cartão", value=v_car, key=f"e_car_{eid}")
                    new_imp = st.number_input(
                        "🧾 Imposto (R$)", value=v_imp, key=f"e_imp_{eid}",
                        help="Informativo — já descontado do caixa"
                    )

                    st.markdown("**Lotes:**")
                    e_pg = st.number_input("Peso (kg)", min_value=None, value=None, placeholder="Ex: 146.5", key="e_p")
                    e_ag = st.number_input("Preço @", min_value=None, value=None, placeholder="Ex: 220.0", key="e_at_val")
                    e_sx = st.radio("Sexo", ["Macho", "Fêmea"], horizontal=True, key="e_sx")

                    if st.button("➕ Adicionar Lote à Edição"):
                        if e_pg and e_ag:
                            st.session_state.lista_gados_edit.append({
                                'peso': e_pg, 'preco': e_ag,
                                'sexo': 'M' if e_sx == 'Macho' else 'F'
                            })
                            st.toast("Lote adicionado!")
                            st.rerun()
                        else:
                            st.warning("Preencha peso e preço.")

                    if st.session_state.lista_gados_edit:
                        for i, g in enumerate(st.session_state.lista_gados_edit):
                            sexo_label = "♂ Macho" if g.get('sexo', 'M') == 'M' else "♀ Fêmea"
                            st.info(f"Lote {i+1}: {g['peso']} kg a R$ {g['preco']}/@ — {sexo_label}")
                        if st.button("🗑 Limpar Lotes da Edição"):
                            st.session_state.lista_gados_edit = []
                            st.rerun()

                    # Extras da feira em edição
                    st.markdown("**🍖 Extras desta Feira:**")
                    st.caption("Fígado, bucho, mocotó etc. — valor já está no caixa.")
                    col_eex1, col_eex2 = st.columns([3, 1])
                    with col_eex1:
                        desc_extra_edit = st.text_input("Item", placeholder="Ex: Fígado", key="e_ex_desc")
                    with col_eex2:
                        valor_extra_edit = st.number_input("Valor (R$)", min_value=None, value=None, placeholder="0,00", key="e_ex_val")
                    if st.button("➕ Adicionar Extra", key="add_extra_edit"):
                        if desc_extra_edit.strip() and valor_extra_edit and valor_extra_edit > 0:
                            registrar_extra(id_edit, desc_extra_edit.strip(), valor_extra_edit)
                            st.rerun()
                        else:
                            st.warning("Preencha item e valor.")

                    extras_edit = session.query(ExtraFeira).filter_by(id_feira=id_edit).all()
                    if extras_edit:
                        for e in extras_edit:
                            col_ei, col_eb = st.columns([4, 1])
                            col_ei.write(f"• {e.descricao}: R$ {float(e.valor):.2f}")
                            if col_eb.button("🗑", key=f"del_extra_edit_{e.id}"):
                                excluir_extra(e.id)
                                st.rerun()
                        st.info(f"Total extras: **R$ {sum(float(e.valor) for e in extras_edit):.2f}**")

                    if st.button("Salvar Alterações", use_container_width=True):
                        if not st.session_state.lista_gados_edit:
                            st.error("Adicione ao menos um lote.")
                        else:
                            ok = atualizar_feira_completa(
                                id_edit, new_in, new_out, new_pix, new_car,
                                new_imp,
                                st.session_state.lista_gados_edit
                            )
                            if ok:
                                st.success("Atualizado!")
                                st.session_state.edit_feira_id = None
                                st.session_state.lista_gados_edit = []
                                st.rerun()
            else:
                st.info("Nenhuma feira ativa para editar.")
    finally:
        session.close()
