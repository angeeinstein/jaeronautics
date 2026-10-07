/**
 * The membership form, from both doors: the public signup (/join), which
 * makes the login too, and a membership for somebody signed in without one
 * (/account/create-membership), which uses their login's address. The rules
 * are the server's (forms.py, through aeronautics_members/api/signup.py): what
 * each kind of member is asked comes from GET /api/v1/forms/options, the texts
 * to accept from GET /api/v1/legal -- each opens over the form, so reading one
 * loses nothing. The answer is where the browser goes next: Stripe's payment
 * page, the thank-you page for an invoice, or My Account.
 */
import {
  Alert,
  Anchor,
  Button,
  Checkbox,
  Group,
  Modal,
  PasswordInput,
  Radio,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Fragment, useEffect, useRef, useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { LegalTextBody } from '../../components/legal/LegalText';
import { Panel } from '../../components/Panel';
import { ErrorState, LoadingState } from '../../components/States';
import { emptyToNull } from '../../lib/forms';
import { messageOf } from '../../lib/notify';
import { type FormOptions, formOptionsQuery } from '../account/shared';

type Profile = Omit<Schemas['MembershipIn'], 'terms_accepted' | 'payment_method'>;
type PaymentMethod = Schemas['MembershipIn']['payment_method'];
type LegalLink = Schemas['LegalTextLinkOut'];

/** The public signup, or a membership for the login of ``email``. */
export type Door = { kind: 'signup' } | { kind: 'profile'; email: string };

const EMPTY: Profile = {
  salutation: '',
  title: '',
  first_name: '',
  last_name: '',
  street: '',
  house_number: '',
  postal_code: '',
  city: '',
  country: '',
  phone_private: '',
  phone_work: '',
  email_work: '',
  member_category: '',
  year_group: '',
};

/** The fields with a place for the server's word on them. */
const SHOWN = new Set([...Object.keys(EMPTY), 'email_private', 'password', 'terms_accepted']);

function initial(options: FormOptions): Profile {
  const has = (list: { value: string }[], value: string) => list.some((choice) => choice.value === value);
  return {
    ...EMPTY,
    country: has(options.countries, 'Austria') ? 'Austria' : '',
    member_category: has(options.member_categories, 'student')
      ? 'student'
      : (options.member_categories[0]?.value ?? ''),
  };
}

/** One text to accept, over the form. */
function LegalTextDialog({ reading, onClose }: { reading: LegalLink | null; onClose: () => void }) {
  const slug = reading?.slug ?? null;
  const text = useQuery({
    queryKey: ['legal', slug ?? '', null, null] as const,
    queryFn: () => call(api.GET('/api/v1/legal/{slug}', { params: { path: { slug: slug ?? '' } } })),
    enabled: slug !== null,
  });
  return (
    <Modal opened={slug !== null} onClose={onClose} size="xl" title={reading?.name} centered>
      {text.isPending ? (
        <LoadingState />
      ) : text.isError ? (
        <ErrorState error={text.error} onRetry={() => void text.refetch()} />
      ) : (
        <LegalTextBody text={text.data} />
      )}
    </Modal>
  );
}

/** "the Statutes, the Rules of Procedure and the Privacy Policy", each a link that opens it. */
function TextLinks({ texts, onOpen }: { texts: LegalLink[]; onOpen: (text: LegalLink) => void }) {
  return (
    <>
      {texts.map((text, index) => (
        <Fragment key={text.slug}>
          {index === 0 ? 'the ' : index === texts.length - 1 ? ' and the ' : ', the '}
          <Anchor
            href={text.url}
            target="_blank"
            rel="noopener"
            underline="always"
            onClick={(event) => {
              // A new tab when asked for one; else the text over the form.
              if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault();
              onOpen(text);
            }}
          >
            {text.name}
          </Anchor>
        </Fragment>
      ))}
    </>
  );
}

function AcceptLabel({ onOpen }: { onOpen: (text: LegalLink) => void }) {
  const legal = useQuery({ queryKey: ['legal'] as const, queryFn: () => call(api.GET('/api/v1/legal')) });
  const texts = legal.data?.texts.filter((text) => text.accepted_at_signup) ?? [];
  if (!texts.length) {
    return (
      <>
        I accept the{' '}
        <Anchor component={AppLink} to="/legal" target="_blank" underline="always">
          legal texts
        </Anchor>
        .
      </>
    );
  }
  return (
    <>
      I accept <TextLinks texts={texts} onOpen={onOpen} />.
    </>
  );
}

function Refusal({ error }: { error: Error }) {
  const exists = error instanceof ApiError && error.code === 'account_exists';
  return (
    <Alert color={exists ? 'amber' : 'red'} variant="light" role="alert">
      <Stack gap="xs">
        <Text size="sm">{messageOf(error)}</Text>
        {exists ? (
          <div>
            <Button component={AppLink} to="/login" size="xs">
              Sign in
            </Button>
          </div>
        ) : null}
      </Stack>
    </Alert>
  );
}

export function MembershipForm({ door }: { door: Door }) {
  const options = useQuery(formOptionsQuery);
  if (options.isPending) return <LoadingState />;
  if (options.isError) return <ErrorState error={options.error} onRetry={() => void options.refetch()} />;
  return <Form door={door} options={options.data} />;
}

function Form({ door, options }: { door: Door; options: FormOptions }) {
  const [value, setValue] = useState(() => initial(options));
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>('checkout');
  const [accepted, setAccepted] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [reading, setReading] = useState<LegalLink | null>(null);
  // Counts the server's refusals: after each, the first field it refused gets the focus.
  const [refusals, setRefusals] = useState(0);
  const form = useRef<HTMLFormElement>(null);
  const kind = options.member_categories.find((category) => category.value === value.member_category);

  const send = useMutation({
    mutationFn: () => {
      const membership = {
        ...value,
        title: emptyToNull(value.title),
        phone_work: emptyToNull(value.phone_work),
        email_work: emptyToNull(value.email_work),
        year_group: kind?.year_group === 'hidden' ? null : emptyToNull(value.year_group),
        payment_method: paymentMethod,
        terms_accepted: accepted,
      };
      return door.kind === 'signup'
        ? call(api.POST('/api/v1/signup', { body: { ...membership, email_private: email, password } }))
        : call(api.POST('/api/v1/account/membership', { body: membership }));
    },
    onMutate: () => {
      setErrors({});
    },
    onSuccess: ({ go_to }) => {
      window.location.assign(go_to);
    },
    onError: (error) => {
      if (error instanceof ApiError) setErrors(error.fields);
      setRefusals((count) => count + 1);
    },
  });

  // After the server's answer -- not while a refused field is corrected -- to the first field it refused.
  useEffect(() => {
    if (refusals) form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [refusals]);

  const clear = (key: string) => {
    if (errors[key]) setErrors({ ...errors, [key]: '' });
  };
  const change = (key: keyof Profile, next: string) => {
    setValue({ ...value, [key]: next });
    clear(key);
  };
  const text = (key: keyof Profile) => ({
    value: value[key] ?? '',
    error: errors[key] ?? null,
    onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
      change(key, event.currentTarget.value);
    },
  });
  // A refusal of the form as a whole, not of one of its fields.
  const refusedAsAWhole =
    send.error &&
    !(send.error instanceof ApiError && Object.keys(send.error.fields).some((key) => SHOWN.has(key)));
  const onwards = paymentMethod === 'checkout' ? ' and continue to payment' : '';

  return (
    <form
      ref={form}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        send.mutate();
      }}
    >
      <Stack gap="lg">
        <Panel title="You">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Salutation"
              required
              data={options.salutations}
              value={value.salutation || null}
              error={errors.salutation ?? null}
              onChange={(next) => {
                change('salutation', next ?? '');
              }}
            />
            <TextInput label="Title" autoComplete="honorific-prefix" maxLength={50} {...text('title')} />
            <TextInput
              label="First name"
              required
              autoComplete="given-name"
              maxLength={100}
              {...text('first_name')}
            />
            <TextInput
              label="Last name"
              required
              autoComplete="family-name"
              maxLength={100}
              {...text('last_name')}
            />
            <Select
              label="Membership type"
              required
              data={options.member_categories.map(({ value: category, label }) => ({
                value: category,
                label,
              }))}
              description={kind?.description}
              value={value.member_category || null}
              error={errors.member_category ?? null}
              allowDeselect={false}
              onChange={(next) => {
                change('member_category', next ?? '');
              }}
            />
            {kind?.year_group === 'hidden' ? null : (
              <TextInput
                label="Year group"
                description="Letters and two digits, for example LAV25."
                placeholder="LAV25"
                required={kind?.year_group === 'required'}
                maxLength={50}
                {...text('year_group')}
              />
            )}
          </SimpleGrid>
        </Panel>

        <Panel title="Address">
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <TextInput
              label="Street"
              required
              autoComplete="address-line1"
              maxLength={255}
              {...text('street')}
            />
            <TextInput label="House number" required maxLength={20} {...text('house_number')} />
            <TextInput
              label="Postal code"
              required
              autoComplete="postal-code"
              maxLength={20}
              {...text('postal_code')}
            />
            <TextInput
              label="City"
              required
              autoComplete="address-level2"
              maxLength={100}
              {...text('city')}
            />
            <Select
              label="Country"
              required
              data={options.countries}
              searchable
              value={value.country || null}
              error={errors.country ?? null}
              onChange={(next) => {
                change('country', next ?? '');
              }}
            />
          </SimpleGrid>
        </Panel>

        <Panel title="Email and phone">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            {door.kind === 'signup' ? (
              <TextInput
                label="Private email"
                description="Your login. Not a university address: it must keep working after you leave."
                required
                type="email"
                autoComplete="email"
                maxLength={255}
                value={email}
                error={errors.email_private ?? null}
                onChange={(event) => {
                  setEmail(event.currentTarget.value);
                  clear('email_private');
                }}
              />
            ) : (
              <TextInput
                label="Private email"
                description="Your login's address."
                value={door.email}
                readOnly
                error={errors.email_private ?? null}
              />
            )}
            <TextInput
              label="Private phone"
              required
              type="tel"
              autoComplete="tel"
              maxLength={50}
              {...text('phone_private')}
            />
            <TextInput
              label="University or company email"
              description={
                kind?.university_email_required
                  ? 'Confirms that you study here, for example name@edu.fh-joanneum.at.'
                  : undefined
              }
              required={kind?.university_email_required}
              type="email"
              maxLength={255}
              {...text('email_work')}
            />
            <TextInput label="Work phone" type="tel" maxLength={50} {...text('phone_work')} />
          </SimpleGrid>
        </Panel>

        {door.kind === 'signup' ? (
          <Panel title="Your password">
            <PasswordInput
              label="Password"
              description="At least 8 characters."
              required
              autoComplete="new-password"
              maxLength={128}
              value={password}
              error={errors.password ?? null}
              onChange={(event) => {
                setPassword(event.currentTarget.value);
                clear('password');
              }}
            />
          </Panel>
        ) : null}

        <Panel title="Paying">
          <Stack gap="md">
            {options.invoice_payments ? (
              <Radio.Group
                label="Payment method"
                value={paymentMethod}
                onChange={(next) => {
                  setPaymentMethod(next === 'invoice' ? 'invoice' : 'checkout');
                }}
              >
                <Group gap="lg" mt="xs">
                  <Radio value="checkout" label="Card or SEPA Direct Debit" />
                  <Radio value="invoice" label="Invoice" />
                </Group>
              </Radio.Group>
            ) : null}
            <Text size="sm" c="dimmed">
              {paymentMethod === 'invoice'
                ? 'We email you the invoice.'
                : 'You pay on the next page. If needed, you can finish paying later from your account.'}
            </Text>
            <Checkbox
              label={<AcceptLabel onOpen={setReading} />}
              checked={accepted}
              error={errors.terms_accepted ?? null}
              onChange={(event) => {
                setAccepted(event.currentTarget.checked);
                clear('terms_accepted');
              }}
            />
          </Stack>
        </Panel>

        {refusedAsAWhole ? <Refusal error={send.error} /> : null}
        <Button type="submit" size="md" loading={send.isPending || send.isSuccess} fullWidth>
          {`${door.kind === 'signup' ? 'Join' : 'Start membership'}${onwards}`}
        </Button>
      </Stack>
      <LegalTextDialog
        reading={reading}
        onClose={() => {
          setReading(null);
        }}
      />
    </form>
  );
}
