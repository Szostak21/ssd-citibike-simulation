# Wnioski Końcowe i Rekomendacja Inżynierska

## 1. Rekomendacja Inżynierska

> **Optymalna konfiguracja dla dolnego Manhattanu (383 stacje poniżej 59th St, 22 401 doków) to $N = 4$ furgonetki relokacyjne (o pojemności $K = 25$ rowerów, zmiana 06:00–22:00) sterowane strategią predykcyjną (Scenariusz C) z horyzontem prognozy $H = 60\text{ min}$ i progami bezpieczeństwa $10\%/90\%$, co redukuje nieobsłużony popyt o $59.9\%$ (ratując średnio $7,727$ operacji klientów dziennie i podnosząc poziom obsługi z $89.15\%$ do $95.65\% \pm 0.12\%$) oraz skraca czas niedostępności stacji o $59.9\%$ (o $30,572$ stacjo-minut dziennie) przy koszcie operacyjnym $324.9 \pm 8.2\text{ km}$ dziennie ($23.88 \pm 0.91$ uratowanego klienta na $1\text{ km}$ furgonetki).**

---

## 2. Kluczowe wnioski operacyjne i Playbook Wdrożeniowy

1. **Przewaga predykcji nad reaktywnością przy tej samej flocie ($N = 4$, H2 & H3):** Strategia predykcyjna C podnosi poziom obsługi z $92.93\%$ (Scenariusz B) do **$95.65\%$**, jednocześnie zmniejszając dzienny przebieg furgonetek z $378.9\text{ km}$ do **$324.9\text{ km}$** ($-14.3\%$ km) i podwajając efektywność kosztową kilometra z $11.87$ do **$23.88\text{ os./km}$** ($+101.2\%$). 
2. **Substytucja wielkości floty inteligencją algorytmu (H4 & H5):** Punkt $N = 4$ stanowi kolano krzywej Pareto (przekroczenie progu $95\%$ obsłużonego popytu). Mniejsza flota predykcyjna **C ($N = 4$)** osiąga wyższy poziom obsługi (**$95.65\%$**) niż o $50\%$ większa flota reaktywna **B ($N = 6$: $93.72\%$)**, pozwalając zaoszczędzić **2 etaty/furgonetki** oraz **$249.4\text{ km}$ przebiegu dziennie** ($-43.4\%$). 
3. **Harmonogram prewencyjnego drenażu i zasilania stref:**
   - **06:15–07:45 (przed szczytem porannym):** Wyprzedzające opróżnianie stacji węzłowych w Midtown i Financial District (`9 Ave & W 33 St`, `E 47 St & Park Ave`, `E 40 St & Park Ave`, `Broadway & W 25 St`) do adaptacyjnego pułapu $22\%\,C_i$ i przerzut rowerów do stref mieszkaniowych (Lower East Side, East Village, Stuyvesant Town, West Village).
   - **14:45–16:15 (przed szczytem popołudniowym):** Odwrócenie wektora relokacji — wyprzedzające doładowanie stacji biurowych w Midtown do $90\%\,C_i$ przed falą wyjazdów o 17:00–18:30.
4. **Dynamiczna modulacja floty w dni deszczowe (Reguła Pogodowa):** Podczas dni z opadem ciągłym ($\ge 0{,}5\text{ mm/h}$, empiryczny mnożnik popytu $w_{\text{rain}} = 0{,}55$) do utrzymania poziomu obsługi $> 95\%$ wystarczają **$N = 2$ furgonetki predykcyjne** ($\sim 160\text{ km/dobę}$), co pozwala skierować pozostałe 2 pojazdy do prac serwisowych.
