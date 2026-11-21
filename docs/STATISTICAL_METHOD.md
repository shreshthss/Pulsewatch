# PW-Scan Statistical Detection Method

## 1. Adaptive Initial Baseline
The detector selects an initial block duration $L \in \{60, 300, 900, 3600\}$ seconds, choosing the smallest block where expected event count $E_L \ge 20$. Initial rate is estimated robustly using median block velocity.

## 2. Sticky Contamination Masking & Refitting
To protect the baseline from being inflated by sustained surges or severe spikes:
1. Candidate windows are screened across fine timescales using standardized residual threshold $Z \ge 4.0$.
2. Elevated intervals are added to a sticky exclusion mask.
3. The baseline rate $\tilde{r}$ and median absolute deviation ($\text{MAD}$) are refit on clean (unmasked) coverage seconds.
4. Up to 5 refitting iterations are performed until the masked set converges.
5. The fraction of time masked and events masked are recorded in the report.

## 3. Scale-Dependent Dispersion Model
For window scale $w \in \{10, 60, 300\}$:
$$\phi(w) = \frac{\operatorname{Var}(S_w)}{\operatorname{E}(S_w)}$$
Sample variance is estimated from clean, non-overlapping blocks with a Poisson lower bound $\phi(w) \ge 1.0$.

When $\phi(w) > 1.05$, count dynamics are modeled using Negative-Binomial parameters:
$$r = \frac{E}{\phi(w) - 1}, \qquad q = \frac{E}{r + E}$$

## 4. Numerically Defensible Tail Evaluation
Upper tail probability $P(X \ge k)$ is computed using stable log-space summation:
- For Poisson: $\ln \text{pmf}(j) = -E + j \ln E - \ln \Gamma(j+1)$.
- For Negative-Binomial: $\ln \text{pmf}(j) = \ln \Gamma(j+r) - \ln \Gamma(r) - \ln \Gamma(j+1) + r \ln(1-q) + j \ln q$.
Geometric series convergence bounds provide a rigorous numerical truncation error estimate. If truncation limits are reached, the report discloses `tail_exact=false` and bounds the omitted probability mass.

## 5. Reliability States & Abstention
- `HIGH`: $\ge 600$s continuous coverage, $\ge 85\%$ coverage ratio, low volatility, verified deduplication.
- `MEDIUM`: $\ge 180$s continuous coverage, $\ge 60\%$ coverage ratio.
- `LOW`: High contamination ($>40\%$ time masked), regime asymmetry outside $[0.4, 2.5]$, or deduplication capacity degradation.
- `ABSTAIN`: Observation time $< 60$s, coverage $< 40\%$, or total event count $< 20$.
