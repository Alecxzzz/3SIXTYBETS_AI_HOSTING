import db
import dashboard

r = dashboard.resolver_picks_finalizados()
print("RESULTADO:", r)
print(db.run_query("select result, count(*) as c from ai_picks group by result"))
