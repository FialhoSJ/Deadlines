```markdown
# 🐳 Dockerman Deadline System

Sistema de **deadlines** com contagem regressiva contínua, barra de progresso visual e persistência em banco de dados.

## Funcionalidades

- Contagem regressiva **ao vivo** (atualiza a cada 1 segundo)
- Barra de progresso com cores de urgência baseadas em dias restantes:
  - **> 30 dias** → Verde (Tranquilo)
  - **15 a 30 dias** → Amarelo (Atenção)
  - **7 a 15 dias** → Laranja (Urgente)
  - **< 7 dias** → Vermelho (Crítico)
  - **Atrasado** → Vermelho
- Cadastro de novos deadlines
- Listagem de todos os deadlines (ativos e finalizados)
- Edição de título, descrição e data limite
- Finalizar / Reabrir deadlines
- Exclusão permanente
- Filtros: Ativos / Finalizados / Todos
- Interface moderna e responsiva

## Tecnologias

| Camada     | Tecnologia                          |
|------------|-------------------------------------|
| Backend    | Python 3 (stdlib apenas)            |
| Banco      | SQLite (persistente)                |
| Frontend   | HTML + Tailwind CSS + JavaScript    |
| Container  | Docker + Docker Compose             |

## Estrutura do Projeto

```
deadline-system/
├── app.py                 # Backend (API REST + SQLite)
├── public/
│   └── index.html         # Frontend completo
├── Dockerfile
├── docker-compose.yml
└── data/                  # Banco SQLite (criado automaticamente)
```

## Como rodar com Docker (recomendado)

```bash
cd deadline-system
docker compose up -d --build
```

Acesse: **http://localhost:8080**

### Comandos úteis

```bash
# Ver logs
docker compose logs -f

# Parar
docker compose down

# Reiniciar
docker compose restart
```

Os dados ficam salvos no volume Docker `deadlines-data` e não se perdem ao reiniciar o container.

## Como rodar sem Docker (desenvolvimento)

```bash
cd deadline-system
python3 app.py
```

Acesse: **http://localhost:8000**

## API

| Método | Endpoint              | Descrição                    |
|--------|-----------------------|------------------------------|
| GET    | `/api/deadlines`      | Lista todos os deadlines     |
| POST   | `/api/deadlines`      | Cria um novo deadline        |
| PUT    | `/api/deadlines/{id}` | Atualiza um deadline         |
| DELETE | `/api/deadlines/{id}` | Exclui um deadline           |

### Exemplo de criação (POST)

```json
{
  "title": "Entregar relatório",
  "description": "Relatório mensal",
  "due_at": "2026-08-15T18:00:00.000Z"
}
```

## Cores de Urgência

A cor da barra e do tempo restante é definida pela quantidade de **dias restantes**:

| Dias Restantes | Cor      | Status    |
|----------------|----------|-----------|
| Mais de 30     | Verde    | Tranquilo |
| 15 a 30        | Amarelo  | Atenção   |
| 7 a 15         | Laranja  | Urgente   |
| Menos de 7     | Vermelho | Crítico   |
| Atrasado       | Vermelho | Atrasado  |

---

Feito com 🐳 por **Dockerman**
```