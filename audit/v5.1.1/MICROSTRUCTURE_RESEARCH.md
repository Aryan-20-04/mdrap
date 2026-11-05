# MDRAP v5.1.1 — Quantitative Market Microstructure Research

## 1. Literature Review & Theoretical Foundations
This report documents the mathematical research and microstructure algorithms implemented in `packages/mdrap-strategies/mdrap_strategies/microstructure.py`:
1. **Avellaneda & Stoikov (2008)**: "High-frequency trading in a limit order book", *Quantitative Finance*, 8(3), 217-224.
2. **Stoikov (2018)**: "The Micro-Price: A High-Frequency Estimator of Future Prices", *Quantitative Finance*, 18(12), 1959-1966.
3. **Cont, Kukanov & Stoikov (2014)**: "The Price Impact of Order Book Events", *Journal of Financial Econometrics*, 12(1), 47-88.
4. **Lee & Ready (1991)**: "Inferring Trade Direction from Intraday Data", *The Journal of Finance*, 46(2), 733-746.

---

## 2. Mathematical Models & Closed-Form Solutions

### 2.1 Avellaneda-Stoikov Closed-Form Reservation Price & Optimal Spread
Under standard utility maximization for an agent with constant absolute risk aversion (CARA) $\gamma$ facing arithmetic Brownian motion price dynamics:

$$dS_t = \sigma dW_t$$

The reservation price (indifference price) $r(s, q, t)$ satisfies:

$$r(s, q, t) = s - q \cdot \gamma \cdot \sigma^2 \cdot (T - t)$$

Where:
- $s$: prevailing mid price
- $q$: current inventory position
- $\gamma$: risk-aversion coefficient ($\gamma > 0$)
- $\sigma$: price volatility
- $T - t$: normalized remaining trading horizon

The optimal bid and ask quotes around reservation price $r$ are given by:

$$\delta^a + \delta^b = \gamma \sigma^2 (T - t) + \frac{2}{\gamma} \ln\left(1 + \frac{\gamma}{\kappa}\right)$$

$$\delta = \frac{1}{2} (\delta^a + \delta^b)$$

$$r_{bid} = r - \delta, \quad r_{ask} = r + \delta$$

Where $\kappa$ parameterizes liquidity intensity $\lambda(\delta) = A e^{-\kappa \delta}$.

#### Sensitivity Analysis:
- When $q > 0$ (long inventory), $r < s$: The reservation price shifts down, causing the bid quote to drop (discouraging buying) and the ask quote to drop closer to mid (incentivizing selling).
- When $q < 0$ (short inventory), $r > s$: The reservation price shifts up, causing the ask quote to rise (discouraging selling) and the bid quote to rise closer to mid (incentivizing buying).
- As volatility $\sigma \to \infty$, the optimal spread $\delta$ widens quadratically, protecting the market maker from adverse selection.

---

### 2.2 Volume-Weighted Micro-Price (Stoikov 2018)
Standard mid-price $P_{mid} = \frac{P_a + P_b}{2}$ ignores order book queue depth. The volume-weighted micro-price $P_{micro}$ is defined as:

$$P_{micro} = \frac{Q_b P_a + Q_a P_b}{Q_b + Q_a} = P_{mid} + \frac{S}{2} \cdot \left(\frac{Q_b - Q_a}{Q_b + Q_a}\right)$$

Where $S = P_a - P_b$ is the prevailing bid-ask spread.
- When $Q_b \gg Q_a$, $P_{micro} \to P_a$ (buying pressure predicts upward mid-price jump).
- When $Q_a \gg Q_b$, $P_{micro} \to P_b$ (selling pressure predicts downward mid-price jump).

---

### 2.3 Multi-Level Order Flow Imbalance (Cont, Kukanov & Stoikov 2014)
Order Flow Imbalance ($OFI_t$) captures the net supply/demand innovation across top-of-book updates:

$$OFI_t = \Delta B_t - \Delta A_t$$

Where:

$$\Delta B_t = \begin{cases} 
q^b_t, & \text{if } P^b_t > P^b_{t-1} \\
q^b_t - q^b_{t-1}, & \text{if } P^b_t = P^b_{t-1} \\
-q^b_{t-1}, & \text{if } P^b_t < P^b_{t-1}
\end{cases}$$

$$\Delta A_t = \begin{cases} 
q^a_t, & \text{if } P^a_t < P^a_{t-1} \\
q^a_t - q^a_{t-1}, & \text{if } P^a_t = P^a_{t-1} \\
-q^a_{t-1}, & \text{if } P^a_t > P^a_{t-1}
\end{cases}$$

In our implementation, this formula produces a symmetric metric:
- $OFI > 0$: Net buying pressure.
- $OFI < 0$: Net selling pressure.

---

### 2.4 Lee-Ready (1991) Trade Aggressor Classification
To determine whether a public trade print was buyer- or seller-initiated:
1. **Quote Rule**:
   - If $P_{trade} > P_{mid} \implies$ BUY aggressor (+1).
   - If $P_{trade} < P_{mid} \implies$ SELL aggressor (-1).
2. **Tick Rule** (if $P_{trade} == P_{mid}$):
   - If $P_{trade} > P_{prev\_trade} \implies$ BUY aggressor (uptick).
   - If $P_{trade} < P_{prev\_trade} \implies$ SELL aggressor (downtick).
   - If $P_{trade} == P_{prev\_trade} \implies$ Inherit prior classification (zero-tick).

---

## 3. Structural Assumptions & Model Limitations
In our empirical audit, we identified key limitations of the theoretical models:
1. **Poisson Arrival Assumption**: Real market orders arrive in clustered, self-exciting Hawkes processes rather than homogeneous Poisson processes.
2. **Constant Volatility**: The Avellaneda-Stoikov model assumes constant instantaneous volatility $\sigma$; in practice, volatility exhibits intraday seasonality (U-shaped smile) and GARCH clustering.
3. **Zero Execution Latency**: Pure theoretical models assume immediate quote posting, ignoring the 1–5ms exchange wire latency and matching engine queuing delays.
