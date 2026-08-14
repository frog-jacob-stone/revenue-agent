import { FileSignature } from 'lucide-react';
import PlaceholderPage from '../components/shared/PlaceholderPage';

/**
 * Contracts — placeholder.
 *
 * Nothing in this repo models a contract. The terms that behave like contract
 * terms are scattered: `contracted_fees` lives in the Airtable rev rec ledger,
 * and payment terms, billing type, and draw schedules live in this system's
 * billing group config. Neither is a contract record.
 */
export default function Contracts() {
  return (
    <PlaceholderPage
      title="Contracts"
      subtitle="Signed agreements and the commercial terms downstream billing depends on."
      icon={FileSignature}
    >
      <p>
        Nothing in this system models a contract today. The terms that act like contract terms
        are split across two places: <code>contracted_fees</code> in the Airtable revenue
        ledger, and payment terms, billing type, and draw schedules in billing group config.
      </p>
      <p>
        Whether this becomes a record of its own or a view over what already exists is an open
        question — it has not been scoped or designed.
      </p>
    </PlaceholderPage>
  );
}
