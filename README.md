SGB - Sistema de Gestão de Banca (v2.2)
    O SGB é uma solução de automação financeira e operacional desenvolvida para gerenciar uma banca de carne bovina. O sistema substitui registros informais por uma base de dados estruturada, oferecendo métricas de precisão sobre lucro real, gestão de fiados e eficiência de lotes.

Funcionalidades Principais (Conforme DET v2.2)
    Registro de Feiras: Suporte a múltiplos lotes de gado com cálculos automatizados de custo baseados em arrobas (RN01).  

    Gestão de Fiados (Módulo de Clientes): Controle de débitos e créditos com atualização automática de saldo devedor (RF03/RF04).  

    Dashboard de BI: Visualização reativa de lucro líquido, médias mensais e gráfico de dispersão para análise de Margem % vs. Peso (RF05).  

    Monitoramento de Quebra: Aplicação automática de 10% de quebra sobre o peso bruto para estimativa de peso limpo vendável (RN03).  

    Segurança de Dados: Implementação de lixeira (Soft Delete) para recuperação de registros (RF06).  

    Backup: Exportação de dados operacionais para formato .csv (RF07).

Tecnologias Utilizadas
    Linguagem: Python 3.10+  

    Framework Web: Streamlit  

    ORM: SQLAlchemy  

    Banco de Dados: SQLite

Como Executar o Projeto
    1. **Clone o repositório**:
   ```bash
   git clone [https://github.com/TrajetoriaTech/gestao-sgb.git](https://github.com/TrajetoriaTech/gestao-sgb.git)
   cd gestao-sgb

   2. **Crie e ative um ambiente virtual**:
   ```bash
   python -m venv venv
   # No Windows:
   .\venv\Scripts\activate

   3. Instale as dependências:
   pip install -r requirements.txt

   4. Execute a aplicação:
   streamlit run app.py